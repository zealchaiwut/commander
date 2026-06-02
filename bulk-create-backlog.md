# Commander Frontend-First Milestone — Bulk-Create Backlog

Paste **one code block at a time** into the bulk-create tab's prompt textarea,
set the **sprint label** + **default labels** noted in each section's header, pick
concurrency, and run. Prompts are separated by `---` exactly as the bulk-create
splitter expects. Review/edit the BA drafts before posting.

**Usability legend:**
- 🟢 pure frontend — live on next page refresh, no redeploy (usable this trip)
- 🟡 mostly frontend, one or two pieces touch the backend
- 🔴 backend — needs the uvicorn redeploy you can do when you're home

Order is by usability + your stated priority (authoring/estimation/UX up top;
autonomy/observability lowered but kept).

---

## Sprint 1 — Bulk-create authoring polish 🟢
**Labels:** `sprint-1`, `frontend` · the tab you're already editing; highest "use it tonight" leverage.

```
After the BA drafts each ticket in the bulk-create tab, render the drafted title and body as editable fields on each result card so I can fix wording or tighten the acceptance criteria before posting to GitHub. Edits must persist into the post-selected payload so the posted issue reflects my edits, not the original draft. File: apps/dashboard/static/project.html, bulk-create pane.
---
Add a per-row "Regenerate draft" button on each bulk-create result card that re-runs the BA draft for that single prompt only, without re-running the whole batch. Show a per-row loading state while it regenerates and replace just that card's title/body on completion.
---
Add a "Remove from batch" control on each bulk-create result row so I can exclude a drafted ticket before posting. The post-selected button count and the selected set must update to reflect removed rows.
---
Let me reorder prompts in the bulk-create input before drafting — either drag-to-reorder or move-up/move-down buttons on a parsed list view of the prompts. Preserve the chosen order through drafting and through posting to GitHub.
---
Detect duplicate prompts in the bulk-create textarea before submitting, using a normalized (trimmed, case-insensitive, whitespace-collapsed) comparison, and visually highlight the duplicate prompts with a warning so I can remove them.
---
Add reusable prompt templates to the bulk-create tab, stored in localStorage and keyed per project: save the current textarea content as a named template, insert a saved template into the textarea, and delete templates. Show the saved templates in a small dropdown or list near the textarea.
---
Improve the bulk-create prompt splitter so it can also split on markdown headings (lines starting with # or ##) in addition to the --- separator, producing one ticket per heading. Make the split mode user-selectable (by --- or by heading) so I can paste a structured markdown doc and get one ticket per section.
---
Show a live ticket count and per-prompt word/character count as I type in the bulk-create textarea, and display a warning as I approach the 50-prompt batch cap so I don't silently exceed it.
---
Add "Select all", "Deselect all", and "Select failed" controls to the bulk-create results so I can quickly choose which drafted tickets to post or retry, with the post-selected button reflecting the current selection count.
---
Persist the bulk-create form state — textarea content, chosen default labels, and concurrency — to localStorage so a page refresh or accidental navigation does not lose a long batch of prompts. Restore it on next load and offer a clear/reset control.
```

---

## Sprint 2 — Sprint board (kanban) UX 🟢
**Labels:** `sprint-2`, `frontend` · faster to drive a 5-sprint / 50-ticket plan on screen.

```
Add multi-select to the sprint-mgmt kanban board: let me select multiple ticket cards (checkbox or modifier-click), then bulk-move all selected tickets to another sprint in one action using the existing plan endpoint. Show how many are selected and clear the selection after the move.
---
Add a search/filter bar to the sprint-mgmt board that filters visible ticket cards in real time by title text, issue number, and label. Filtering must apply across all sprint columns at once.
---
Show a header summary on each sprint column on the board: ticket count, the sprint goal text, and a progress bar of done versus total tickets for that sprint.
---
Add collapse/expand toggles to each sprint column on the sprint-mgmt board so a 5-sprint plan fits on a laptop screen, and persist each column's collapsed state in localStorage so it survives a refresh.
---
Add a label filter chip row above the sprint-mgmt board to toggle visibility of tickets by label (backlog, sit, uat, in-progress, etc.). Toggling a chip hides/shows matching cards across all columns.
---
Add a compact/dense view toggle to the sprint-mgmt board that shrinks ticket card height to fit more tickets per column without scrolling. Persist the chosen density in localStorage.
---
Show the estimator size badge (S/M/L/XL) on each ticket card on the sprint-mgmt board, reading from the cached estimate JSON, with a clear neutral "unestimated" state when no estimate exists for that ticket.
---
Add keyboard navigation to the sprint-mgmt board: arrow keys move focus between ticket cards, and a keyboard shortcut moves the focused card to the next or previous sprint via the plan endpoint.
---
Add a per-column sort control to the sprint-mgmt board so I can sort tickets within a sprint by issue number, estimated size, or title.
---
Make the sprint-mgmt board toolbar (search, label filters, deselect, refresh, view/density toggles) sticky so it stays visible when scrolling a long board.
```

---

## Sprint 3 — Estimate-in-the-loop 🟡
**Labels:** `sprint-3`, `frontend` · display reads cached estimates (live now); re-estimate calls backend.

```
Display the estimator's size and confidence inline on each ticket card across the sprint board and the tickets tab, reading from the cached estimate JSON, and show a clear "not estimated" state when no estimate exists. File: apps/dashboard/static/project.html.
---
Add a per-sprint ETA rollup on each kanban column header that sums the cached estimates for that sprint's tickets into an approximate total time and ticket count (e.g. "~2h, 6 tickets").
---
Add an estimate detail popover on a ticket card that shows size, confidence, files likely affected, and risk flags from the cached estimate when I click an "estimate" affordance on the card.
---
Add a "needs estimate" filter and badge to the board so I can quickly see which tickets in a sprint have no cached estimate yet.
---
Show estimate risk flags (touches-db-schema, security-sensitive, breaks-tests) as warning icons on the ticket card, each with a tooltip naming the risk, read from the cached estimate.
---
Add a sprint-level summary panel that aggregates risk flags and any file-overlap warnings across all tickets in a sprint, shown before I run that sprint so I can spot conflicts up front.
---
Add a "Re-estimate" button on a ticket card that calls the estimator endpoint with force and refreshes the displayed estimate for that ticket. This one needs the backend estimator endpoint, so it goes live after a redeploy.
---
Color-code ticket cards by estimated size (S/M/L/XL) on the sprint-mgmt board so I can visually scan whether a sprint is balanced or front-loaded with large tickets.
---
Display total estimated tokens/cost per sprint on the column header when that data exists in the cached estimate, so I can see the rough spend of a planned sprint before running it.
```

---

## Sprint 4 — Ticket quality assistant 🟡
**Labels:** `sprint-4`, `frontend` · better ticket inputs → testable AC the tester can actually verify.

```
Add an acceptance-criteria template picker to the single-ticket modal and the bulk-create flow that inserts a structured, testable AC scaffold into the ticket body/prompt (given/when/then or checklist style) so drafted tickets carry verifiable criteria.
---
Add a client-side ticket-quality linter to the bulk-create and new-ticket forms that flags vague prompts — too short, no action verb, no acceptance signal — and warns me before submit, without blocking if I choose to proceed.
---
Add a "this prompt may be too large — consider splitting" heuristic warning in the bulk-create flow, based on prompt length and the presence of multiple distinct asks, so oversized tickets get split before drafting.
---
Add a required-fields nudge to the new-ticket modal so a ticket cannot be posted without a title and a minimum-length body, with inline validation messages.
---
Add a UAT-steps template scaffold option alongside the AC template in the ticket-creation flows, so drafted tickets include testable UAT steps for the tester to follow.
---
Add an editable per-project default-labels presets list to the ticket-creation UI, stored in localStorage, so I can quickly apply my common label sets when creating single or bulk tickets.
---
Add collapsible inline help to the bulk-create tab showing a good-prompt vs bad-prompt example so I can write better prompts without leaving the page.
---
Add a markdown structure preview and character count to the new-ticket modal body field so I can see how the issue body will render before posting.
```

---

## Sprint 5 — Estimation accuracy / calibration 🔴
**Labels:** `sprint-5`, `backend` · the real "more accurate estimation"; needs redeploy when home.

```
Build an estimate-vs-actual report: for finished sprints, compare each ticket's estimated size to its actual elapsed time from the saved sprint state, and expose the comparison via a new API endpoint the dashboard can read.
---
Add a calibration view to the dashboard that shows the estimator's historical bias per size bucket — the average actual time for tickets estimated S, M, L, and XL on this project.
---
Feed historical calibration data into the estimator's prompt/logic so the size-to-time mapping reflects this project's actual pace rather than the generic S=1-5min / M=15min defaults.
---
Flag tickets or labels the estimator has historically mis-sized (large gap between estimate and actual) so they surface for human review before a sprint runs.
---
Add file-overlap conflict detection across tickets in a pending sprint and expose the warnings via API so the board can show which tickets touch the same files and may conflict.
---
Persist per-ticket actual elapsed time and token totals in a queryable form (DB or structured state) so the calibration report and rollups can be computed reliably across sprints.
---
Add a dependency-order hint derived from the file-overlap DAG to the sprint view, flagging tickets that should run in a specific order to avoid conflicts.
```

---

## Sprint 6 — Morning observability 🔴
**Labels:** `sprint-6`, `backend` · "what happened last night" while you were in another timezone; needs redeploy.

```
Generate a per-sprint "morning report" that summarizes the run — shipped, failed, and blocked tickets with one-line outcomes and a diff stat each — written into the sprint summary and exposed via API for the dashboard to display.
---
Add a failure-triage API and view: list every failed or needs-rework ticket with its failure reason and the relevant tail of its agent log, so I can triage in the morning without digging through raw log files.
---
Add a token and elapsed-time rollup per sprint and per ticket, surfaced in the dashboard, so a runaway ticket that burned disproportionate time or tokens is immediately obvious.
---
Add a "what shipped" feed for a sprint listing each merged ticket with a link to its diff, so I can review the night's output quickly.
---
Add a per-ticket timeline to the ticket detail view showing coder start/done, tester start/done, and gate result with timestamps.
---
Surface blocked or stuck tickets prominently in the dashboard, showing how long each has been in its current state so stalls stand out.
---
Add a sprint outcome banner at the top of the project view after a run completes, summarizing X shipped / Y failed / Z blocked.
```

---

## Sprint 7 — Agent autonomy & self-healing 🔴
**Labels:** `sprint-7`, `backend` · makes unattended overnight runs actually trustworthy; needs redeploy.

```
Add per-agent stuck/timeout detection in sprint_manager: if a coder or tester subprocess exceeds a configurable wall-clock limit, kill it and mark the ticket failed instead of letting the run hang indefinitely.
---
Add an auto-rework loop so that when a quality gate fails, the failure context is fed back to the coder for one automatic retry before the ticket is marked needs-rework.
---
Write a structured failure diagnosis to the ticket when a ticket fails — which gate failed, the key error, and a suggested cause — so morning triage is fast and actionable.
---
Add heartbeat detection: if an agent subprocess produces no new log output for a configurable number of minutes, flag the ticket as possibly stuck so it can be surfaced or killed.
---
Make sprint resume more robust: on resume, verify the branch and worktree state and recover cleanly from a partially dispatched ticket rather than assuming a clean state.
---
Add a configurable max-retries-per-ticket with backoff before the run gives up on a ticket, so transient failures don't permanently kill otherwise-good tickets.
---
Detect tickets whose dependencies failed and skip them with a clear "skipped: dependency failed" status, rather than dispatching them into the same failure.
```

---

## Sprint 8 — Project status & living docs 🟡
**Labels:** `sprint-8`, `docs` · a living status you can hand to a Claude session for context; status view is frontend, doc generation is backend.

```
Generate a living STATUS.md at the project root that summarizes current state — open issues grouped by label and sprint, what's in SIT and UAT, recent merges, and the current sprint goal — regenerated on demand and after each sprint. Keep it concise and readable by both me and a Claude session so it can be pasted in as context to bring a new conversation up to speed.
---
Add a "Status" view or tab to the project dashboard that renders the current project status — counts per label, the active sprint and its goal, recent activity, and links to in-flight tickets — reading live from the existing issues and sprint APIs. File: apps/dashboard/static/project.html.
---
Wire the existing documenter agent to run after a sprint merges and update README, CHANGELOG, and SCHEMA docs from the sprint diff, committing the doc updates to the sprint branch so docs ship with the code.
---
Add a "context digest" export that produces a single markdown summary of the project's current state, active work, and recent decisions, formatted for pasting into a new Claude Code session so the assistant starts with full context.
---
Add a CHANGELOG that the documenter appends to per shipped ticket — issue number and a one-line summary — so there is a running, human-readable history of what shipped and when.
---
Add a docs-freshness check that flags when README, CLAUDE.md, or SCHEMA have not been updated despite code changes in related areas, and surface the stale-docs warning in the dashboard.
---
Keep the current sprint goal and per-sprint progress updated in STATUS.md automatically as tickets move through the board, so the status doc never goes stale.
---
Add a per-project notes doc, stored in the repo, that the dashboard can read and write — for decisions, todos, and running context — so I can keep notes that persist across sessions and conversations rather than losing them in chat.
```

---

## Sprint 9 — Project Settings tab + branch hygiene 🔴
**Labels:** `sprint-9`, `backend` · new Settings tab to home maintenance tools; branch ops touch git, so they go live after a redeploy. Destructive actions must confirm and guard protected branches.

```
Add a new "Settings" tab to the project view as a home for per-project configuration and maintenance actions, starting with the branch hygiene tools below. File: apps/dashboard/static/project.html.
---
In the Settings tab, list all branches (local and remote) for the project with last-commit date, author, ahead/behind counts versus develop, merged status, and the associated issue number when it can be derived from the branch name.
---
Identify and clearly label stale branches in the Settings tab — branches already merged into develop or master, or with no commits in a configurable number of days — so they are easy to spot at a glance.
---
Add a one-click "Delete branch" action (local and remote) in the Settings tab, behind a confirmation dialog, that NEVER allows deleting master, develop, or any configured protected branch.
---
Add multi-select and bulk-prune for stale branches in the Settings tab, with a confirmation summary that lists exactly which branches will be deleted before anything runs.
---
Show, for each feature branch, whether it maps to a closed or merged issue, so I can confirm a branch is genuinely safe to delete before pruning it.
---
Add a backend script (scripts/prune_branches.py) that lists and deletes merged or stale branches, with a dry-run mode and hard guards against protected branches (master, develop), used by the Settings tab.
---
Add a dry-run preview to the branch-prune flow that shows exactly what would be deleted without deleting anything, so I can review before committing to a prune.
---
Surface a stale-branch count badge on the project (sidebar or Settings tab) so I know when branch cleanup is due without having to go looking.
```
