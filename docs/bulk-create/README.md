# Bulk Create — Prompt & Output Record

This folder is the durable record of every bulk-create batch run against the
dashboard. Each file holds the prompts that were pasted into the Bulk Create
tab plus the issues they produced, so a batch can be reviewed, re-run, or copied
later without reconstructing it from memory.

## Why this exists

The Bulk Create tab runs a BA agent per prompt to draft tickets, then an
estimator to size them. The prompts themselves are valuable — they encode how a
feature set was scoped — but they live only in the browser textarea once pasted.
Saving them here keeps that scoping work, and gives both the human and Claude a
shared place to plan the next batch.

## Naming

```
docs/bulk-create/YYYY-MM-DD-N-<topic>.md
```

One file per batch. `N` is the batch's sequence within the day (1, 2, 3, …)
so same-day batches sort in creation order. Dates use Bangkok time. Examples:
- `2026-06-11-1-machine-onboarding.md`
- `2026-06-11-2-sprint-reporting-analytics.md`

(Files from before 2026-06-11 keep their unsequenced names; rename forward only.)

## File format

```markdown
# <Batch title>

**Date:** YYYY-MM-DD
**Sprint label:** sprint-N (or NEW)
**Default labels:** frontend, enhancement
**Status:** drafted | posted | run

## Prompts

Paste one code block at a time into the Bulk Create textarea. Prompts are
separated by `---` exactly as the splitter expects.

\```
<prompt 1>
---
<prompt 2>
\```

## Posted issues

| # | Title | Size |
|---|-------|------|
| 681 | … | M |
```

## How it is used

- **Human:** open the relevant file, copy the prompt block, paste into Bulk
  Create, review the BA drafts, post.
- **Claude:** when asked to draft a batch of prompts, write them here first,
  then point the human at the file to paste from.

## Existing records

- [2026-frontend-first.md](2026-frontend-first.md) — frontend-first authoring/UX backlog
- [2026-sprint-runner-enhancements.md](2026-sprint-runner-enhancements.md) — sprint manager / runner enhancements
- [2026-06-09-agent-browser-testing.md](2026-06-09-agent-browser-testing.md) — agent-browser live UAT + impeccable skills
- [2026-06-09-analytics-nav-logging.md](2026-06-09-analytics-nav-logging.md) — analytics tabs/calcs, nav moves, global notes, agent logging
- [2026-06-09-deploy-restart-env.md](2026-06-09-deploy-restart-env.md) — per-project deploy/restart (Mac mini + Render) + Render-style env editor
- [2026-06-09-sprint-pipeline.md](2026-06-09-sprint-pipeline.md) — level-bounded coder∥tester pipeline + stale-in-progress fix
- [2026-06-10-deploy-tab-followups.md](2026-06-10-deploy-tab-followups.md) — Deploy tab refinements (scoping, folder/port, live log, Start/Stop, headless gh auth)
- [2026-06-10-settings-and-timezone.md](2026-06-10-settings-and-timezone.md) — Global Settings consolidation (away from modal) + machine-local timezone
- [2026-06-10-logs-surface-failures.md](2026-06-10-logs-surface-failures.md) — surface real failure class/message on the Logs page + fix board "tester rejected" mislabel
- [2026-06-11-1-machine-onboarding.md](2026-06-11-1-machine-onboarding.md) — launchd PATH from real tool locations, headless tokens at install, machine doctor, onboarding runbook
- [2026-06-11-2-sprint-reporting-analytics.md](2026-06-11-2-sprint-reporting-analytics.md) — finish-sprint reconciliation, verified New Sprint, logs per-agent time, real analytics, documentor sprint briefs
- [2026-06-11-3-roadmap-milestones.md](2026-06-11-3-roadmap-milestones.md) — phase 1: GitHub-native milestones, Roadmap tab, ticket↔milestone, home progress
- [2026-06-11-4-sprint-planner-signoff.md](2026-06-11-4-sprint-planner-signoff.md) — phase 2: planner from active milestone, pending-signoff + approve, scheduled night queue (off by default)
- [2026-06-11-5-advisor.md](2026-06-11-5-advisor.md) — phase 3: daily advisor suggestions, accept→BA flow, 2–5 sprint look-ahead, brief hook
- [2026-06-11-6-test-suite-rehab.md](2026-06-11-6-test-suite-rehab.md) — pytest-timeout + hang fix, schema-drift fixtures, failure burn-down, per-sprint suite-health gate
- [2026-06-20-1-sprint-speed-phases.md](2026-06-20-1-sprint-speed-phases.md) — Phase 1 runner quick wins, Phase 2 concurrent engine, Phase 3 preview-dag plan order + XL split
- [2026-06-29-1-board-aggregate-api.md](2026-06-29-1-board-aggregate-api.md) — board aggregate API
- [2026-06-29-2-running-history-aggregate.md](2026-06-29-2-running-history-aggregate.md) — running+history aggregate refactor (sprint 2)
- [2026-07-02-1-p1-critical-bugs.md](2026-07-02-1-p1-critical-bugs.md) — P1s from 2026-07-02 audit: milestones POST bug, composite-PK poisoning, scheduler deadlock, RETRY_FREE drop
- [2026-07-02-2-p2-backend-orchestrator-bugs.md](2026-07-02-2-p2-backend-orchestrator-bugs.md) — P2 backend/orchestrator bugs from the same audit
- [2026-07-02-3-p2-scripts-hooks-frontend-bugs.md](2026-07-02-3-p2-scripts-hooks-frontend-bugs.md) — P2 scripts/hooks/frontend bugs from the same audit
- [2026-07-03-1-dead-code-purge.md](2026-07-03-1-dead-code-purge.md) — delete dead files/endpoints, last 'planned'-state writer, archive orphaned one-shot scripts
- [2026-07-03-2-frontend-call-reduction.md](2026-07-03-2-frontend-call-reduction.md) — visibility guard, timer dedup, SSE-driven board, home N+1 batch (26→8 calls/min idle)
- [2026-07-03-3-github-mirror-routing.md](2026-07-03-3-github-mirror-routing.md) — route mirror-bypassing gh call sites through the issues/milestones mirror
- [2026-07-03-4-aggregate-coherence.md](2026-07-03-4-aggregate-coherence.md) — SSE invalidation push, shared aggregate cache, api-volume observability, call-budget harness, flag cutover, cache-inventory doc
- [2026-07-03-5-mobile-nav-modals.md](2026-07-03-5-mobile-nav-modals.md) — mobile 390px fixes: swipeable tab strip, modal height cap, 44px touch targets, horizontal-overflow purge, deploy pane
