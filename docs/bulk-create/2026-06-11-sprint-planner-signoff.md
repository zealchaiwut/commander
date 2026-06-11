# Roadmap phase 2 — sprint planner, sign-off, scheduled night runs

**Date:** 2026-06-11
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

CEO loop: review the proposed next sprint in the evening, sign off, it runs
overnight, the morning brief shows results. Decisions: manual run first —
scheduled auto-run ships as a capability that is OFF by default; sprints can
queue in sequence for the night. Depends on phase 1
([2026-06-11-roadmap-milestones.md](2026-06-11-roadmap-milestones.md)).

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Add a sprint planner that drafts the next sprint from the active milestone. A "Plan next sprint" action (button on the board and on the Roadmap tab) assembles a proposed sprint from the active milestone's open backlog tickets: respects the sprint capacity setting (the existing Cap field), prefers estimated tickets and runs the estimator for unsized ones, orders by the existing DAG/dependency preflight, and carries over unfinished tickets from the last sprint first. The result is created as a normal sprint label with tickets assigned, in a new pending-signoff state. Acceptance: clicking Plan next sprint produces a filled sprint card whose tickets come from the active milestone, within capacity, dependency-ordered, marked pending sign-off.
---
Add a pending-signoff state with an explicit Approve step. A planned sprint card shows a PENDING SIGN-OFF badge and cannot be run (Run Sprint disabled) until approved. I can edit it freely while pending — drag tickets in and out, reorder, change capacity — using the existing board interactions. An Approve button (with one confirm) moves it to the normal ready state; a Reject button dissolves the sprint back to backlog. Record who/when approved in the plan file and the activity log. Acceptance: a planned sprint is clearly marked, editable, blocked from running until I approve it, and approval is logged.
---
Add scheduled sprint runs with a sequential night queue, off by default. A project-level setting "Scheduled run time" (e.g. 01:00, machine-local time) plus a per-sprint "Run on schedule" toggle that only appears on approved sprints. At the scheduled time the dashboard runs approved-and-scheduled sprints one at a time in board order — next one starts only when the previous finishes, respecting the existing one-sprint-per-project lock. Failures stop the queue and alert. Everything is off unless the setting is configured and the sprint opted in; manual Run Sprint keeps working unchanged. Acceptance: with a schedule set and two approved sprints opted in, both run sequentially overnight without intervention, and with the setting empty nothing auto-runs.
---
Surface planner state in the morning brief and evening flow. The daily brief (sprint 64 feature) gains two sections: "ran overnight" with each scheduled sprint's outcome and a link to its sprint brief, and "waiting on you" listing any pending-signoff sprint with its ticket count and estimated hours. The sprint nav pill shows a small indicator when a sprint awaits sign-off. Acceptance: the morning brief tells me what ran and what needs my sign-off tonight, and the nav hints when a plan is waiting.
```

## Notes

- Planner reuses: Cap field, estimator, DAG preflight, rerun carry-over logic.
  No new planning engine — assembly + ordering only; the advisor (phase 3)
  is the one that thinks.
- Scheduler: in-dashboard (asyncio loop checking the configured time), not
  cron — survives via launchd KeepAlive; missed-window (dashboard down) runs
  are skipped, not replayed.
- Manual-first per decision: everything works without a schedule; the queue
  is additive.

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
