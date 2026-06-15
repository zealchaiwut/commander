# Run-start ticket-set completeness guard

**Date:** 2026-06-13
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

Incident: sprint-66.2 ran **6 of 8** tickets. `plan.json` listed all 8, but at
run start (06:25) the re-run relabel hadn't propagated to the local issues
mirror, so `sprint_manager` loaded only the 6 it could see and **silently
dispatched a partial set** — #861/#862 never ran (they still carried the label
on GitHub). The dashboard rerun path already waits for relabel (#938), but the
orchestrator's own ticket load has no such guard. Goal: a run never silently
runs a partial set — it re-syncs, and if it still can't load every intended
ticket, it refuses to start and says which tickets are missing.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Add a run-start ticket-set completeness guard to sprint_manager so a sprint never silently runs a partial set of tickets. When a run starts, after the orchestrator loads the dispatchable tickets carrying the sprint label, reconcile that loaded set against the sprint's plan.json `tickets` list, counting a plan ticket as "expected" only if it is still open AND still carries the sprint label on a FRESH read (a ticket intentionally closed or unlabeled is not missing). If any expected ticket is absent from the loaded set — the common cause is a re-run relabel that hasn't reached the local issues mirror yet — re-sync the issues mirror from GitHub and re-load, retrying up to a small bounded number of attempts over a few seconds. If the loaded set is complete, proceed normally. If expected tickets are still missing after the retries, DO NOT start a partial run: stop before dispatching, record a clear failure/blocked status in the sprint state and the activity log naming the missing ticket numbers and the reason (e.g. "run blocked: #861, #862 carry sprint-66.2 but were not loaded — mirror not in sync"), and exit without dispatching any coder/tester. Acceptance: (1) re-running a sprint whose tickets were just relabeled loads the FULL set after an automatic re-sync and runs all of them; (2) if an expected ticket genuinely cannot be loaded, the run refuses to start rather than dispatching a subset, and the recorded status names exactly which tickets are missing and why; (3) a sprint whose plan ticket was deliberately closed/unlabeled still runs (that ticket is not treated as missing).
---
Surface the completeness-guard "run blocked" state on the sprint board and offer a one-click retry. When the run-start guard refuses to start a run because expected tickets weren't loaded, the board shows that sprint in a distinct blocked state (not a generic crash) with the specific missing ticket numbers and a one-line reason, plus a Retry action that re-syncs the issues mirror and re-attempts the run. The blocked state and reason come from the status the guard wrote (no re-derivation on the client). Acceptance: when a run is blocked by the guard, the board clearly shows which tickets are missing and why, and Retry — once the mirror has caught up — starts the full run with all expected tickets; nothing auto-runs a partial set.
```

## Notes

- Backend guard reuses the existing mirror sync (`sync_issues_mirror`) and the
  `_is_dispatchable` / label classification already in `sprint_manager.py`; the
  reconcile compares the loaded set against `plan.json` `tickets` filtered to
  still-open-and-still-labeled on a fresh GitHub read.
- Complements #938 (dashboard rerun relabel-wait): that waits before the run is
  dispatched; this guards the orchestrator's own load at run start, so a partial
  set can't slip through from any entry point (rerun, resume, scheduled run).
- Fail-loud over best-effort: refusing a partial run is the point — the 66.2
  incident was a silent 6/8 run that looked complete.

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
