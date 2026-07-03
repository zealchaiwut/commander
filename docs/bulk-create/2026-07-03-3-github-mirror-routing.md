# GitHub API Quota — Route Bypass Call Sites Through the Mirror

**Date:** 2026-07-03
**Sprint label:** NEW
**Default labels:** backend, performance
**Status:** drafted

Source: 2026-07-03 four-agent full review (GitHub API volume audit against
develop @ 7520d393). The core client is already well-optimized: the 60s
ETag-conditional issues mirror (github_events_sync) costs ~0 quota on 304s,
and github_client._cached() gives 30s+ TTLs on the gh fallback path. The
waste is router and sprint_manager code calling gh directly, bypassing both.
Estimated effect: metrics/mis-sizing/estimates/summaries tabs drop from
~1 GraphQL per sprint per load to 0; a 20-ticket sprint run drops from
~100 GraphQL + 80 REST to ~80 GraphQL + ~0 avoidable REST.

Overlap note: the list_milestones triplicate-definition bug already has a
drafted P1 ticket (2026-07-02-1-p1-critical-bugs.md, prompt 1: dedup + fix
POST-instead-of-GET). The prompt here covers only the mirror-backing and
caching layer on top — if the P1 ticket has not landed first, the coder
should land the dedup as prerequisite work within that ticket, not here.

## Prompts

```
Route the four analytics-family routers through the issues mirror instead of live gh issue list calls.

Four router call sites bypass both the mirror and _cached(), firing live GraphQL gh issue list on every request: (1) routers/metrics.py:56 _count_rework_tickets runs gh issue list --label sprint-N --label needs-rework once PER SPRINT inside /api/metrics/sprints — a 15-sprint metrics view costs ~15 GraphQL per load; (2) routers/mis_sizing.py:291 pulls gh issue list --state all --limit 1000 (the heaviest single call in the app) per /api/mis-sizing request; (3) routers/estimates.py:867 runs gh issue list --state all --label UAT --limit 200 per estimates request; (4) routers/sprint_summaries.py:463 runs its own gh issue list per request. All of this data is already in the local issues mirror (db.get_mirrored_issues, refreshed every 60s by github_events_sync at ~0 quota).

Fix: route each through github_client's mirror-backed reads (group_issues_by_sprint / _mirror_issues / list_all_open_issues), falling back to the existing gh path only when the mirror is unpopulated. For metrics._count_rework_tickets specifically, replace the per-sprint subprocess with ONE mirror pass that counts needs-rework per sprint label for all sprints. Preserve exact response shapes.

AC must cover: with a populated mirror, /api/metrics/sprints, /api/mis-sizing, the estimates endpoint, and the summaries endpoint issue zero gh subprocess calls (assert via monkeypatched _gh runner); responses byte-identical for a fixture mirror; empty-mirror fallback still works; per-sprint loop in metrics gone.
---
Rewrite reconciliation.gather_inputs_via_gh to read the ticket roster from the mirror — N+2 GraphQL calls per reconcile → ≤1.

services/sprint_manager/reconciliation.py:316/327/345-348 gather_inputs_via_gh does one gh issue list, one gh pr view, then a PER-TICKET gh issue view loop (:346-348) — all GraphQL. A 20-ticket sprint reconcile costs ~22 GraphQL calls for data (labels, state, body) the issues mirror already holds.

Fix: fetch the roster once from the mirror (_mirror_issues filtered by sprint label) for both tickets and summary issues; keep exactly one live call for PR merge state — gh pr view, or cheaper gh api repos/{r}/pulls/{n} (REST budget). Fall back to the current gh path only when the mirror is unpopulated (reconciliation can run from CLI on machines without the dashboard — preserve that path). Note the mirror is ≤60s stale; reconcile runs at sprint end where that staleness is acceptable, but document it in the docstring.

AC must cover: reconcile of an N-ticket fixture sprint issues ≤1 gh subprocess for issue data (0 with mirror, plus the single PR-state call); CLI fallback without mirror still produces identical RecInputs; existing reconciliation tests pass.
---
Read dispatched-issue bodies from the mirror instead of per-ticket gh api fetches in the run loop.

Three call sites fetch each ticket's body individually via gh api repos/.../issues/N during a sprint run: dispatch.py:363 _fetch_dispatch_issue_body (per ticket dispatched), sprint_manager.py:2258 (per ticket in dispatch), and estimate_issue.py:147 (per ticket estimated). The mirror already stores body — a 20-ticket run burns ~20-60 avoidable REST subprocess round-trips.

Fix: add/use a github_client helper (e.g. _mirror_issue(repo, n)) that returns the mirrored issue including body, falling back to the existing gh api fetch on mirror miss; switch all three call sites. Freshness guard: a just-created or just-edited ticket may be newer than the 60s mirror — on mirror hit, if updated_at is older than the run's start-of-dispatch mirror sync (or the mirror lacks the issue), fall through to the live fetch. Keep estimate_issue's write paths (gh issue comment/edit) untouched.

AC must cover: with a populated mirror, dispatching N fixture tickets performs zero per-ticket body fetches (monkeypatched runner assertion); mirror-miss and stale-hit fall back to live fetch; dispatched prompt content identical for fixture bodies.
---
Make latest_active_sprint mirror-backed and move label_transitions label reads off GraphQL.

(a) github_client.py:765 latest_active_sprint runs gh issue list --state open --limit 500 (GraphQL) on every 30s cache miss — on a hot dashboard ~120 GraphQL/hr per repo — and is the only major github_client read NOT mirror-backed. Fix: derive it from group_issues_by_sprint() (mirror): highest sprint number with a non-closed issue; keep the gh path only for unpopulated mirror. (b) services/sprint_manager/label_transitions.py:122 _get_issue_labels uses gh issue view --json labels (GraphQL) where state_machine.py:101 _fetch_labels already proves the REST form (gh api repos/{r}/issues/{n}) works; switch it to the REST form or the mirror to move best-effort label reads off the scarce GraphQL budget.

AC must cover: latest_active_sprint returns the same label for a fixture mirror as the gh path (parity test); zero gh subprocess on mirror hit; _get_issue_labels no longer invokes gh issue view (REST or mirror instead); state-machine transition behavior unchanged.
---
Back list_milestones with the milestones mirror and cache the PR lookups.

(a) github_client.py defines list_milestones three times (615, 966, 1064 — F811); the accidental last-definition winner is uncached and not mirror-backed, so every milestone read is a live gh api REST paginate even though github_milestones.py maintains a zero-quota ETag milestones mirror (db.get_mirrored_milestones, synced every 60s). Prerequisite: the 2026-07-02 P1 batch has a drafted ticket fixing the triplicate definitions and the POST-instead-of-GET bug — if it has already landed, this ticket only adds the mirror layer; if not, land the dedup first inside that ticket's scope. This ticket: route the single surviving list_milestones through the milestones mirror with gh fallback on unpopulated mirror, preserving the return shape (active_milestone accepts dueOn/due_on). (b) github_client.py:798/808 get_pr and find_open_pr_for_head are uncached REST calls invoked repeatedly within one finish/merge flow — wrap them with the existing _cached() helper (keys pr:{repo}:{n} and pr_head:{repo}:{branch}, 30s TTL), and invalidate the pr: prefix in the write ops that mutate PRs (create/merge).

AC must cover: exactly one list_milestones definition; mirror hit issues zero gh subprocess; roadmap and plan-next still render milestones from a fixture mirror; repeated get_pr/find_open_pr_for_head calls within 30s hit the cache (call-count assertion); PR-mutating ops invalidate the cache.
```

## Posted issues

| # | Title | Size |
|---|-------|------|
