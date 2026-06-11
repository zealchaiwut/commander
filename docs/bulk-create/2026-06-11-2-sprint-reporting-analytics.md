# Sprint finish reconciliation, logs detail, real analytics, sprint briefs

**Date:** 2026-06-11
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

From operating sprints 59–61. Two small items were fixed directly on
`hotfix/2026-06-11-manual` (PR #845): token tracking into the Cost tab, and
re-run summaries titling as 60.1/60.2 instead of 60. The rest are here.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Add a finish-sprint reconciliation check that reports loose ends. Sometimes a finished sprint leaves things dangling: the executive summary issue was never posted, the sprint PR did not merge (often a conflict), or sprint status labels were not cleaned off the tickets. Today these pass silently and are discovered by accident. After Finish Sprint completes (and also when a run ends on its own), run a reconciliation pass that verifies: summary issue exists for this exact sprint label; the sprint PR merged (if not, capture the reason, for example merge conflict, and which files conflict); no stale status labels remain on the sprint's tickets. Surface the result as a short checklist on the sprint's history card and as an activity-log event, with a clear red item for anything unresolved. Acceptance: when a sprint finishes with an unmerged PR or missing summary, the board shows exactly what was left undone and why, without me digging through GitHub.
---
Make New Sprint creation verified and loud on failure. Sometimes clicking New Sprint appears to work but the sprint never exists (no label created, no tickets moved). Likely causes are a failed GitHub label create or a rate limit, swallowed silently. Make sprint creation verify each step (label created on GitHub, tickets labeled, plan file written) and, on any failure, roll back what partially happened and show a visible error in the dialog naming the failed step and the reason. Retry once on transient failures. Acceptance: New Sprint either fully creates the sprint or shows me exactly what failed; no more silent no-ops.
---
Show per-agent time and richer error detail on the Logs tab run rows. For each run on the Logs tab, show how long the coder and the tester took per ticket (the agent_runs table now has duration_seconds and total_tokens per dispatch), and when a ticket failed, the failure class and message at first level. Complements the drafted logs batch in 2026-06-10-logs-surface-failures.md (failure class on rows, run-level banner, board mislabel fix, Neon noise) — run that batch together with this ticket. Acceptance: opening a run on the Logs tab shows, per ticket, coder time, tester time, tokens, and for failures the real reason, without opening any log file.
---
Wire Analytics Metrics, Status, and Trends to real data. The Metrics, Status, and Trends views under the Analytics tab still render mock/placeholder numbers. Back them with real sources: delivery health from sprint summaries and agent_runs (durations, pass rate, rework rate), status from live label counts, trends across the last N sprints (throughput, avg ticket time, failure rate, tokens per sprint now that agent_runs.total_tokens is populated). Remove the mock data path entirely so the views are either real or empty-state. Acceptance: the numbers on Metrics, Status, and Trends match what the sprint summaries and the database say for recent sprints, and no hardcoded sample values remain.
---
Have the documentor write a per-sprint brief markdown after each sprint. After a sprint finishes, the documentor should write docs/sprint-briefs/sprint-<label>.md (new folder, one file per sprint label including re-runs) answering "what changed and what should I expect": what shipped with one line per ticket in plain language, what is now visible or different in the product and where to find it, what awaits UAT and how to exercise it, and anything that did not ship with the reason. Commit it with the documentor's existing flow and link it from the sprint summary issue. Acceptance: after a sprint finishes, docs/sprint-briefs/ contains a readable brief for that exact sprint label, and the summary issue links to it.
```

## Notes

- **Fixed directly, not in this batch** (PR #845): summary token totals + Cost
  tab population; re-run summary titled with the full label (60.1) so each
  re-run gets its own summary issue instead of updating the parent's.
- The Logs-tab prompt assumes the drafted
  [2026-06-10-logs-surface-failures.md](2026-06-10-logs-surface-failures.md)
  batch runs in the same sprint — schedule them together.
- Sprint-brief folder becomes part of the standard docs structure once stable
  (scaffold_project + documentor docs would then reference it).

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
