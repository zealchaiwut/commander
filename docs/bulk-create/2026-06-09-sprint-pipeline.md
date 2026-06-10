# Sprint pipeline — coder ∥ tester (level-bounded)

**Date:** 2026-06-09
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

Today the sprint engine runs fully serial: for each ticket it does
coder → tester → gates → merge, one ticket at a time, so only one agent ever
runs. The coder sits idle while the tester runs and vice versa. This batch adds
a two-stage pipeline so the coder and tester run concurrently, plus fixes a
stale-label bug surfaced while investigating.

## Verified findings

- Dispatch is a single sequential loop (`_flat_dispatch` in sprint_manager.py) —
  coder→tester→merge per ticket, fully, before the next. Never two agents at once.
- The "two spinning tickets" the user saw (a Level-1 and a Level-2 ticket both
  in-progress) is a **stale `in-progress` label** — Level 2 can't run before
  Level 1 finishes, and only one agent runs, so the second spinner is wrong.

## Design (locked with the user)

- **Concurrency:** exactly **1 coder + 1 tester** at a time (matches the single
  coder/ and tester/ worktrees).
- **Level-bounded:** the pipeline runs **within one dispatch level** only. The
  coder works through the level's tickets; the tester runs one ticket behind,
  concurrently. The coder must NOT start the next dispatch level until every
  ticket in the current level has finished testing and merged. The level
  boundary is a hard barrier — this is the dependency gate and it prevents
  develop drift (a level's feature branches all merge before the next level's
  coders branch off develop).
- **Retry → front:** when the tester rejects a ticket, it goes to the **front**
  of the coder queue so the in-flight ticket is fixed before new work.
- **Retry budget:** keep the existing 3× cap; exhausted ⇒ needs-rework, dropped
  from the pipeline.
- **Opt-in:** ship behind a toggle, serial stays the default. A kill-switch
  falls back to serial.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Fix the stale in-progress label on sprint tickets. When a ticket leaves the active coder/tester slot — because it failed, was retried, or errored — its in-progress label is sometimes left applied, so the sprint board shows it spinning forever even though no agent is working it. In sprint_manager.py, make sure the in-progress label is cleared whenever a ticket stops being actively worked: on a transition to SIT, UAT, needs-rework, or blocked, and on any error path that abandons the ticket. Add a safety sweep at the start and end of a sprint run that removes in-progress from any ticket that is not the one currently being dispatched. Acceptance criteria: at most one ticket shows in-progress at any time during a serial run; a ticket that fails or is retried no longer shows a stuck spinner; finishing or aborting a sprint leaves no ticket stuck in in-progress.
---
Add an opt-in two-stage pipeline so the coder and tester run concurrently within a dispatch level. Today the dispatch loop in sprint_manager.py is fully serial. Add a pipeline mode, enabled by a per-sprint or per-project setting (default off, serial remains the default), with a kill-switch to fall back to serial. In pipeline mode, process one dispatch level at a time with two concurrent workers: a coder worker that pulls tickets from a coder queue and produces a SIT feature branch, and a tester worker that pulls tickets the coder finished from a tester queue and runs the tester plus gates plus merge. Run exactly one coder and one tester at a time — no more. The coder must not begin the next dispatch level until every ticket in the current level has finished testing and merged; the level boundary is a hard barrier. When the tester rejects a ticket, push it to the front of the coder queue for a fix, respecting the existing retry cap of three attempts before marking it needs-rework and dropping it from the pipeline. Keep all existing per-ticket behavior — estimates, gates, documentor, labels, events — unchanged; only the scheduling changes. Acceptance criteria: with pipeline mode on and ten independent tickets in one level, the coder works the next ticket while the tester works the previous one, cutting total run time roughly in half versus serial; the next level never starts until the current level is fully merged; a tester rejection sends the ticket back to the coder first; turning pipeline mode off restores the serial behavior exactly.
---
Make label transitions and the active slot safe for two concurrent agents. With a coder and a tester running at the same time, two tickets are legitimately active at once — one in-progress on the coder and one in SIT on the tester. Audit the label transition logic and any shared sprint state so concurrent updates from the two workers do not race or clobber each other. Ensure the in-progress label applies only to the ticket the coder is actively building, and the SIT to UAT transition applies only to the ticket the tester is handling. Serialize writes to the develop branch so only the tester or merge stage writes develop, one ticket at a time, never two merges at once. Acceptance criteria: during pipeline mode exactly one ticket is in-progress and at most one other is in SIT; no two merges to develop happen at the same time; label state stays correct under concurrency.
---
Update the sprint board to show both active agents during pipeline mode. The sprint management board and the nav pill currently assume a single active agent. In pipeline mode, show the coder's current ticket and the tester's current ticket as two distinct active states at once, each with the correct spinner and label, so it is clear which ticket the coder is building and which the tester is verifying. Show the per-level progress and make the level barrier visible — the next level appears as waiting until the current level fully merges. Acceptance criteria: when the coder works one ticket and the tester works another, the board shows both as active with the right labels; the user can tell coder work from tester work at a glance; the next dispatch level is clearly marked as waiting until the current one completes.
```

## Notes for the implementer

- **Single worktrees** are the natural concurrency limit: one `coder/`, one
  `tester/`. Do not run two coders or two testers.
- **Level barrier** is the dependency mechanism — do not pipeline across levels.
  All current-level merges complete before next-level coders branch off develop.
- **Retry to front** of the coder queue, not the back.
- **Serial stays default.** Pipeline is opt-in with a kill-switch.
- The stale-in-progress fix (ticket 1) is independent and can land first.

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
