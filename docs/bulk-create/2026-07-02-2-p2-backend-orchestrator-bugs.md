# P2 Backend & Orchestrator Bugs — Races, Scoping, Sync Waste

**Date:** 2026-07-02
**Sprint label:** NEW
**Default labels:** bug
**Status:** drafted

Source: `docs/bug-audit-2026-07-02.md`. This batch = P2 findings in the
sprint manager, routers, and GitHub sync (audit items 12–20).

## Prompts

```
Fix worktree pool slot creation: all slots share one branch name; zero slots hangs acquire() forever.

services/sprint_manager/worktree_pool.py:~333: every pool slot worktree is created from the same branch name, so git refuses to create slots 1..K ("branch already checked out") and only slot 0 exists; with K>1 configured the pool silently degrades to 1 slot. Worse, if slot 0 creation also fails, the pool has zero slots and acquire() blocks forever with no error (pairs with the scheduler deadlock ticket). Fix: per-slot unique branch names (or detached checkouts), and make acquire() fail fast with a clear error when the pool has zero healthy slots. AC: K=3 pool yields 3 usable slots; zero-slot pool raises instead of hanging; concurrent coders each get distinct worktrees.
---
Fix retry-round branch hygiene: rebased local feature branch never synced to origin.

services/sprint_manager/worktree.py:~443: the retry-round hygiene step (5b) rebases the stale LOCAL feature/<N> branch onto the sprint branch but never pushes/syncs it to origin/feature/<N>. The next coder attempt (fresh worktree/clone) fetches origin and gets the STALE branch, resurrecting the divergence the hygiene step was meant to fix — divergent-branch failures repeat. Fix: after a successful rebase, force-with-lease push the branch to origin (guarded, with clear logging), or rebase from origin state directly. AC: after a retry round, origin/feature/<N> contains the rebased head; simulated stale-branch retry converges instead of hitting divergent-branch twice.
---
Fix stale-status label sweep: gh api call POSTs (creates issues) instead of GETs.

services/sprint_manager/label_transitions.py:~150: _sweep_stale_status builds a gh api invocation with -f/field params but no -X GET, so gh defaults to POST against the issues endpoint — the sweep either errors or, worse, attempts issue creation; the stale in-progress/SIT label sweep has never worked. Same defect class as the list_milestones P1 — fix together stylistically: add -X GET (and --paginate if needed), plus a repo-wide grep test asserting no gh api read call site passes -f without -X GET. AC: sweep lists issues correctly in a stubbed test; a live dry-run sweep finds and reports stale labels; no unintended POSTs.
---
Fix TOCTOU in POST /api/sprints/run: two concurrent requests both spawn sprint_manager.

apps/dashboard/routers/sprint_run.py:~379: the one-running-sprint-per-project check (_any_sprint_running) and the subsequent spawn are not atomic. Two near-simultaneous run POSTs (double-click, two tabs, retry) both pass the check and both spawn sprint_manager processes; the second overwrites the PID file, leaving the first process untracked (unkillable from the UI, invisible orphan writing the same state files). Fix: serialize check+spawn under a per-project lock (in-process asyncio lock is enough for single-server), and/or create the PID file exclusively (O_EXCL) before spawning, treating existence as running. AC: concurrent run requests → exactly one manager process, second request gets 409; PID file always tracks the live process.
---
Fix /api/sprint-progress serving a stale persisted "running" snapshot forever.

apps/dashboard/routers/sprint_nav.py:~305: the endpoint persists its computed snapshot to runtime/sprint-progress.json and on later calls returns that persisted snapshot when the in-memory status is missing — but a snapshot captured while a sprint was running is served indefinitely after the manager dies (the GitHub-derived fallback below it is unreachable). The nav pill shows a running sprint forever until manual cleanup. Fix: validate the persisted snapshot before serving — if it claims running, cross-check sprint_state.current() (and/or PID liveness) and fall through to the live fallback when contradicted; stamp snapshots with a TTL. AC: kill a manager, nav pill stops showing running within one refresh; persisted snapshot for a terminal sprint never reports running.
---
Fix _manager_pid_file project scoping: PID files resolved under the dashboard's own .commander.

apps/dashboard/routers/sprint_reconcile_service.py:~54: _manager_pid_file builds the PID path from the dashboard clone's own commander dir rather than the TARGET project's .commander/sprints, and takes no project parameter. For any non-commander project the orphan check reads a nonexistent (or wrong project's) PID file: a genuinely orphaned sprint in project X never gets settled (file "absent"), and a same-label sprint in the dashboard's project can be misread as evidence. Fix: resolve the PID path from the target project's repo/commander dir (same resolution the run/spawn path uses), passing project through. AC: orphan reconcile works for a non-commander project in a test with two projects sharing a label; commander behavior unchanged.
---
Add project scope to sprint_ticket_order.

apps/dashboard/db.py:~1844: sprint_ticket_order keys on sprint label only. Two projects with the same label (e.g. both have sprint-66 — the exact collision the composite-key invariant exists for) overwrite each other's dispatch order; the sprint manager then dispatches tickets in the other project's order or references foreign issue numbers. Fix: add project column, PK (label, project), scoped read/write helpers, migration preserving existing rows (backfill project from the sprints table where unambiguous), and update the writer in sprint_manager.py:~594 plus preview/dispatch readers. AC: same-label sprints in two projects hold independent orders; migration keeps existing order for the sole owner; collision regression test.
---
Bound the issues-mirror incremental sweep: stop re-crawling entire issue history on every changed sweep.

apps/dashboard/github_events_sync.py:~351: _fetch_issues_conditional requests page 1 sorted by updated desc with an ETag; on any 200 (any single issue changed in the 60s window) it follows the Link rel=next chain UNCONDITIONALLY — paginating through the repo's ENTIRE issue history (commander: ~1700 issues = 17 pages) every sweep with any change. Steady-state sprint activity changes something most minutes, so the mirror burns ~17x the quota intended and sweep latency grows with repo age. Fix: stop paginating once a page's oldest updated_at is older than the last successful sync watermark (store watermark in sync_state alongside the ETag); keep the full crawl for bootstrap and the periodic open-set reconcile as the safety net. AC: sweep with one changed issue fetches 1 page; watermark persisted and honored; bootstrap and closure reconciliation unaffected; mirror correctness test with paged fixtures passes.
```

## Posted issues

| # | Title | Size |
|---|-------|------|
| — | (not yet posted) | — |
