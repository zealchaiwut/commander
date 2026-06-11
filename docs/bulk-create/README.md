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
docs/bulk-create/YYYY-MM-DD-<topic>.md
```

One file per batch. Examples:
- `2026-06-09-pipeline-gaps.md`
- `2026-frontend-first.md`

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
- [2026-06-11-machine-onboarding.md](2026-06-11-machine-onboarding.md) — launchd PATH from real tool locations, headless tokens at install, machine doctor, onboarding runbook
- [2026-06-11-sprint-reporting-analytics.md](2026-06-11-sprint-reporting-analytics.md) — finish-sprint reconciliation, verified New Sprint, logs per-agent time, real analytics, documentor sprint briefs
