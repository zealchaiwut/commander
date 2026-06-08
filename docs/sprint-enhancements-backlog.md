# Commander Sprint-Management & Runner Enhancements — Bulk-Create Backlog

Companion to [`bulk-create-backlog.md`](bulk-create-backlog.md). Paste **one code
block at a time** into the bulk-create tab, set the **sprint + default labels**
from each section header, pick concurrency, and run. Prompts are `---`-separated
exactly as the splitter expects. Review/edit the BA drafts before posting.

**Usability legend (deploy reality — the Mac mini runner is offline ~2–3 days):**
- 🟢 pure frontend — live on next page refresh, no redeploy (usable now)
- 🟡 mostly frontend, one or two pieces touch the backend (needs uvicorn restart)
- 🔴 backend / runner — needs the restart you'll do when you're home

> **Already done on `man/fixes-4jun` (not tickets):** the headless coder/tester
> were running with only a terse `-p` prompt — no persona — because `claude -p`
> can't use the `/coder` // `/tester` slash commands and doesn't auto-load
> subagents. The runner now passes the `.claude/agents/<role>.md` persona via
> `--append-system-prompt` and sets `CLAUDE_AGENT_ROLE`. **A/B verify this on a
> real ticket the moment the runner is back, before trusting the rework loop.**

Order: frontend/now first (P1–P3), then config (P4), then runner work for after
the restart (R1–R2).

---

## Sprint P1 — Estimate-aware planning board 🟢
**Labels:** `sprint-planning`, `frontend` · reads existing estimate/sprint APIs; live on refresh.

```
Add a per-sprint capacity gauge to the Sprint Mgmt board: let me set a target capacity in hours on each sprint header, and render a fill bar comparing the sprint's existing rolled-up estimated hours to that capacity. The bar is green under capacity, amber at >=90%, and red when over (showing the overage like "3h over"). Persist capacity per project + sprint label in localStorage (MVP, no backend). A sprint with no estimates shows the target but no misleading fill. File: apps/dashboard/static/project.html.
---
Surface stale estimates on the Sprint Mgmt board. /api/sprint-management/issues already returns estimate_stale per issue (the body changed since the cached estimate's body_hash) but it's only used at preflight. Show a "stale" badge next to the size pill on any row whose estimate_stale is true, with a tooltip, and a one-click re-estimate that calls the existing POST /api/issues/{id}/estimate, shows an in-progress state, and clears the badge + updates the size pill on completion without a full reload. File: apps/dashboard/static/project.html.
---
Show ticket dependencies on the planning board and flag cross-sprint ordering violations. Render a compact "depends #N / blocks #N" indicator on each ticket row (reuse the Ticket Detail Panel's existing body-parsing for blocks/blocked-by). When a ticket's dependency is in the backlog or in a LATER sprint than the ticket, mark the row with a warning state and a tooltip naming the offending dependency and where it sits. Re-evaluate after a drag-drop move so warnings clear/appear without reload. File: apps/dashboard/static/project.html.
---
Add an "Estimate all unsized" button to each sprint and backlog header that runs the estimator on every ticket in that group lacking a cached estimate, by looping the existing POST /api/issues/{id}/estimate endpoint with a visible progress count. Skip already-sized tickets. This removes the one-by-one friction before estimate-aware planning. File: apps/dashboard/static/project.html.
```

---

## Sprint P2 — Quick to-do panel 🟢
**Labels:** `frontend` · personal scratch list, localStorage; no backend.

```
Add a quick to-do panel toggled from the left edge of the dashboard: a slide-out drawer where I can add, check off, delete, and reorder simple to-do items. Persist items in localStorage so they survive reloads. Make the todos global (they follow me across projects) with an optional "current project" filter tag. Keep it minimal — no due dates, no backend. File: apps/dashboard/static/ (shared layout / project.html).
---
Add an optional "current project only" filter toggle to the quick to-do panel that hides todos not tagged with the active project, and a small count badge on the toggle handle showing how many open todos exist. Frontend-only, localStorage.
```

> **Follow-up (🔴, later):** sync the quick to-do panel to Notion as an opt-in
> backend integration (token + API + conflict handling). Keep the MVP local;
> this is a separate ticket, not part of P2.

---

## Sprint P3 — Concurrency planner preview 🟢
**Labels:** `sprint-planning`, `frontend` · de-risks the concurrent runner (R2) before any engine work.

```
Add a read-only "concurrency preview" to a sprint that shows how its tickets could run in parallel, computed from the existing dependency + file-conflict data (the preflight DAG / estimates' files_likely_affected). Group tickets into ordered "waves": a wave is a set with all dependencies already satisfied and no shared-file conflicts between members. Render it like "Wave 1: #1, #3, #5 in parallel — Wave 2: #2 (waits on #1), #4 (shares server.py with #3)". Read-only, no runner involvement. File: apps/dashboard/static/project.html.
---
On the concurrency preview, surface why any two tickets can't run together: list the specific shared file(s) for file-conflict pairs and the specific dependency edge for ordering pairs, so I can trust (or correct) the estimator's file predictions before relying on them to drive real concurrent runs.
```

---

## Sprint P4 — Configurable settings (project + global) 🟡🔴
**Labels:** `settings` · scoped config: built-in default → global → project → (sprint). Read/UI can be frontend; the write path needs the restart.

```
Introduce a project settings layer stored in .commander/settings.json (next to sprint.yaml) with a documented schema and a scope-resolution helper (built-in default -> global -> project). Add a read-only project Settings panel in the dashboard that displays the resolved values. Ship the schema + read + panel now; the write endpoint can land on the next deploy. Files: apps/dashboard/server.py (read endpoint), apps/dashboard/static/ (settings panel).
---
Make the size→minutes estimation mapping configurable per project, overriding the global default in sizing.py (S/M/L/XL → minutes). The estimator still assigns letters; rollups, capacity, and cost displays must use the project's mapping when set. Example: project A's M = 20 min while project B's M = 10 min. Surface it in the project Settings panel.
---
Add per-project authoring defaults to the project Settings panel: default ticket labels for new/bulk tickets (replacing the hardcoded "enhancement"), the set of risk flags considered "serious", and an optional project-specific estimator guidance string that gets appended to the estimator prompt so sizing reflects this repo's conventions.
---
Add a global Settings panel for cross-project defaults that projects inherit: default model per agent (BA/coder/tester/estimator), the model→price table used for cost displays, and global limits (bulk prompt cap, body-size threshold, attachment limits). Per-project overrides win over these. (Backend write path — lands on restart.)
```

---

## Sprint R1 — Runner: in-sprint rework loop 🔴
**Labels:** `sprint-runner` · after the restart; depends on the coder/tester persona fix already on this branch.

```
Add a single in-sprint rework trial to the sprint runner: when the tester/gates fail for a ticket, instead of only reverting it to SIT and moving on, re-dispatch the coder ONCE with the failure context (the existing failure sidecar is already injected via _build_failure_suffix), then re-run the tester + gates. Cap at one rework attempt per ticket via a per-ticket counter so a ticket can't loop forever. Only attempt rework for logic/gate failures, not infra/hang failures. File: services/sprint_manager/sprint_manager.py (dispatch loop).
---
Record the rework outcome in the sprint state and summary: for each ticket show whether it passed first try or needed a rework trial, and whether the rework succeeded or the ticket ended in needs-rework. Surface this in the sprint outcome data the dashboard already reads.
```

---

## Sprint R2 — Concurrent conflict-aware runner 🔴
**Labels:** `sprint-runner` · the big engine; gate it behind the P3 preview and build after restart. Sequence the prompts top-to-bottom.

```
Add a warm worktree pool to the sprint runner: at sprint start, create K reusable git worktrees off the sprint base branch, each with its own venv (never copy a venv — recreate per the project's venv rule). Assign tickets to free worktrees; reset (git clean + checkout base) between tickets; tear the pool down at sprint end. On startup, reconcile and prune orphaned worktrees left by a previous crash. File: services/sprint_manager/.
---
Add a conflict-aware concurrent scheduler to the sprint runner. Build a graph from estimates: a dependency edge (depends_on/blocks) is a hard ordering constraint; a file-overlap edge (shared files_likely_affected) means two tickets must NOT run in parallel. Run a worker pool sized to the concurrency cap that pulls the largest eligible set (deps satisfied, no file-overlap with anything already running), dispatching coders concurrently into the warm worktree pool.
---
Make the worker pool role-flexible: a slot runs a task that is either "code ticket X" or "test ticket Y". As a coder finishes, that ticket becomes a test task and the freed slot pulls the next eligible work — so the pool naturally rebalances (e.g. 3 coders early, then 2 coders + 2 testers as tickets complete). Respect the conflict/dependency constraints for both coding and testing.
---
Serialize merges in the concurrent runner: even with parallel branches, merges to the base branch happen one at a time, and a later merge may need a rebase. Never parallelize file-overlapping tickets; when a merge conflicts anyway, attempt an automated rebase and, if it still conflicts, flag the ticket for human resolution instead of failing the whole sprint.
---
Add a multi-lane live view for concurrent runs: extend the existing live sprint view to show each active worker/worktree as its own lane with its current ticket, phase (coding/testing), and tail of output, so I can watch several tickets progress at once.
---
Add a prediction-accuracy feedback loop: after each ticket merges, diff the actual changed files against the estimator's predicted files_likely_affected, store the result, and surface per-project prediction accuracy. Use it to calibrate future conflict scheduling and warn when the predictions are too unreliable to parallelize safely.
```

---

## Notes
- **P1–P3 + P4's read/UI** are usable this trip (refresh-only or read-only endpoints).
- **P4 write path, R1, R2** need the uvicorn restart.
- **R2 is the highest-risk area in the project** — its correctness rests on the estimator's file-touch predictions, so the P3 preview and the R2 accuracy-feedback prompt exist specifically to validate/​calibrate that before parallel runs are trusted.
