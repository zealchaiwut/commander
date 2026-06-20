# Sprint speed — Phase 1 runner, Phase 2 concurrent engine, Phase 3 plan fixes

Companion to [`2026-sprint-runner-enhancements.md`](2026-sprint-runner-enhancements.md) and the
sprint-speed audit (coder-time + parallelism levers). Paste **one code block at a time**
into the bulk-create tab, set the **sprint label + default labels** from each section
header, pick concurrency, and run. Prompts are `---`-separated exactly as the splitter
expects. Review/edit the BA drafts before posting.

**Usability legend:**
- 🟢 pure frontend — live on next page refresh, no redeploy
- 🟡 mostly frontend, one or two pieces touch the backend (needs uvicorn restart)
- 🔴 backend / runner — needs uvicorn restart on the Mac mini runner

**Sequencing (do not skip):**
1. **Merge sprint-85** (pending merge) before any Phase 1 runner work lands on `develop`.
2. **Run sprint-86** (or current planned sprint) with `pipeline_mode` enabled in project
   settings — zero code required for ~40–50% overlap on multi-ticket levels.
3. **Phase 1** — small, low-risk runner patches (new sprint after S85 merge).
4. **Phase 3** — planning-board fixes (can run in parallel with Phase 1 if tickets are
   frontend-only; safe while S86 is planned or running).
5. **Phase 2** — concurrent multi-coder engine; only after Phase 3 preview-dag workflow
   is trusted and estimator file predictions are calibrated.

> **Already shipped (do not re-file):** pipeline mode (#737), size→model routing S→Haiku
> (#789), fix-loop with failure sidecar (#618), consecutive-dup early abort on the **serial**
> path, file-overlap DAG + `GET /api/sprints/{label}/preview-dag` (#809), persona via
> `--append-system-prompt`, merge/label serialization (#738).

---

## Phase 1 — Runner quick wins 🔴
**Sprint label:** NEW (e.g. `sprint-87`) · **Default labels:** `sprint-runner`, `enhancement`
**When:** after sprint-85 merges to `develop`; not mixed into a pending-merge sprint.

Small patches to `services/sprint_manager/` — no worktree pool, no multi-coder pool.
Expected gain: fewer wasted retry loops, less coder search time, better DAG ordering.

```
Port consecutive identical-failure early abort to pipeline dispatch mode. The serial dispatch loop already tracks `_last_failure_sig` and aborts the fix-loop early when the same failure class (or gate failure signature) repeats on back-to-back attempts — e.g. LINT_FAIL then LINT_FAIL — tagging needs-rework instead of burning all three fix rounds. Pipeline mode (`_run_pipeline_dispatch` / `pipeline.run_level`) lacks this guard today. Mirror the serial behaviour: on tester REJECT or gate logic failure, compare the new signature to the previous one for that ticket; if identical, skip re-queue to the coder front and finalize as needs-rework immediately. Acceptance: in pipeline mode, a ticket that fails the lint gate twice with the same failure class ends in needs-rework after two attempts, not three; serial behaviour unchanged; structured log emits `fix_loop_exhausted` or equivalent with reason `consecutive identical`.
---
Inject estimator target paths into every coder dispatch prompt. When `.commander/estimates/issue-<N>.json` exists, prepend a short block to the coder `-p` prompt listing `files_likely_affected` (or `files_touched` when present) as "Start here — do not broad-search the repo unless these paths are insufficient." When no estimate exists, omit the block. Apply to both Claude Code and Cline backends. Acceptance: dispatch log for an estimated ticket shows the injected paths; coder persona and existing failure suffix still append after this block; unestimated tickets behave as today.
---
Extend coder model routing so docs-only and config-only tickets prefer Haiku. Today `_resolve_coder_model` routes size S→Haiku and M/L/XL→Sonnet. Add a second routing path: when the cached estimate has risk flag `docs-only` OR every path in `files_likely_affected` matches a docs/config heuristic (e.g. ends with `.md`, under `docs/`, or is `.yaml`/`.json` config with no `.py`/`.js`/`.ts` paths), route to Haiku regardless of size letter (unless size is XL). Record routing_reason as e.g. `docs-only:flag` or `docs-only:paths`. Make the heuristic overridable via `coder_by_size` / project settings later; ship the default heuristic now. Acceptance: a size-M ticket touching only `docs/workflow.md` and `CHANGELOG.md` dispatches Haiku; a ticket touching `server.py` stays on existing size routing; routing_reason appears in agent_runs and the running pane coder badge.
---
Merge explicit ticket dependencies into sprint dispatch DAG layers. `_build_sprint_dag_layers` today builds layers from file-overlap only (`dag_builder.build_dag`); estimate JSON also carries `depends_on` and `blocks` (parsed from issue bodies by the estimator) but the runner ignores them. Before file-overlap edges, add hard directed edges: for each ticket, for each id in `depends_on`, add edge (dep → ticket); for each id in `blocks`, add edge (ticket → blocked). Detect cycles and surface them the same way as file-overlap cycles (warn + fall back to flat level, do not crash the sprint). Acceptance: ticket B with `depends_on: [A]` never dispatches before A even when file sets are disjoint; preview-dag endpoint uses the same merged graph so board preview matches runner order; cycle in explicit deps returns a visible warning in preview-dag and sprint start logs.
---
Add `## Files to touch` to the feature issue template and wire it into estimation. Extend `.github/ISSUE_TEMPLATE/feature.md` with an optional section where the BA lists repo-relative paths. Teach the estimator agent to prefer explicit paths from that section over inference when present, merging them into `files_likely_affected`. When the section is empty, estimator behaviour is unchanged. Acceptance: a ticket with `## Files to touch` listing `services/sprint_manager/pipeline.py` produces an estimate whose files list includes that path; template renders on GitHub; bulk-create drafts include the section as an empty stub.
```

---

## Phase 2 — Concurrent conflict-aware runner 🔴
**Sprint label:** NEW (e.g. `sprint-88`) · **Default labels:** `sprint-runner`, `enhancement`
**When:** after Phase 1 merges and Phase 3 preview-dag workflow has been used on at least
one real sprint plan. **Highest risk** in this batch — sequence prompts top-to-bottom.

This is the path to "four independent tickets in one level finish in ~one ticket's time."
Requires warm worktrees, a worker pool, and serialized merges.

```
Add a warm git worktree pool for concurrent coder dispatches. At sprint start, create K reusable worktrees off the sprint base branch (K from project setting `max_coder_slots`, default 2, hard cap e.g. 4). Each worktree gets a fresh venv (`python -m venv` + pip install — never copy venvs). Assign each concurrent coder dispatch to a free worktree; on ticket completion reset with `git clean -fdx` and checkout the sprint base branch. Tear the pool down at sprint end. On startup, reconcile and prune orphaned worktrees under `.commander/runtime/worktree-pool/` left by a crash. File: services/sprint_manager/. Acceptance: two coders can run simultaneously in different worktrees without clobbering each other's working tree; pool teardown leaves no stray worktrees; crash mid-sprint followed by resume reconciles orphans safely.
---
Add a conflict-aware concurrent scheduler to the sprint runner. Replace the per-level serial/pipeline queue with a worker pool sized to `max_coder_slots` + `max_tester_slots` (defaults 1 each; pipeline overlap still applies when slots=1). Build the eligibility graph from merged deps + file overlap: `depends_on`/`blocks` are hard ordering; shared `files_likely_affected` means two tickets must NOT code concurrently. Each scheduling tick, pick the largest eligible set whose deps are satisfied and whose files do not overlap with anything already coding. Dispatch coders into the warm worktree pool. Persist `max_coder_slots` and `max_tester_slots` on sprint state so the running pane lane capacity matches reality. Acceptance: three disjoint-file tickets in one DAG level start three coders when slots=3; two tickets sharing `server.py` never code at the same time; level barrier still holds — no ticket from level N+1 starts until level N is fully merged.
---
Make the worker pool role-flexible across code and test tasks. A pool slot runs either "code ticket X" or "test ticket Y". When a coder finishes, that ticket enters the test queue and the freed slot pulls the next eligible task (code or test) respecting the same conflict/dependency rules for testing (only one merge at a time). Natural rebalance: early sprint skews coder-heavy; late sprint skews tester-heavy. Reuse existing `code_fn` / `test_fn` stage callables and merge serialization guards (#738). Acceptance: with slots=2, coder and tester can both be busy on different tickets; merges to develop never overlap; tester rejection still re-queues to coder front for that ticket only.
---
Serialize merges in the concurrent runner with automated rebase. Even with parallel feature branches, merges to the sprint base branch happen one at a time via the existing `develop_merge_guard`. When merge N+1 conflicts because merge N landed, attempt `git rebase` onto the updated base automatically once; if rebase still conflicts, flag the ticket needs-rework with the conflict file list — do not fail the whole sprint. Acceptance: two parallel tickets merging in sequence succeed when changes are disjoint; a true content conflict surfaces as a single-ticket needs-rework with actionable paths; other tickets continue.
---
Populate live sprint snapshot slot capacity from the runner. The running pane already reads `max_coder_slots` and `max_tester_slots` (defaults 1 when absent). Sprint manager must write these fields into the posted status payload at run start from resolved project settings / sprint overrides. When concurrent mode is off (slots=1), behaviour matches today's pipeline/serial UI. Acceptance: concurrent run with slots=3 shows "3 of 3" coder capacity in the lane header; serial run shows 1 of 1; no separate layout path required.
---
Add a multi-lane live view for concurrent runs. Extend the Running pane so each active worktree/worker is its own lane showing ticket id, phase (coding / testing / merging), and tail of that ticket's dispatch log. Reuse existing log tail endpoints and status pills — no new card primitives. Collapse idle slots. Acceptance: during a 3-coder run I can see three lanes with independent log tails; pipeline mode with slots=1 still renders the existing two-lane coder/tester view.
---
Add estimator prediction-accuracy feedback after each ticket merges. Diff actual changed files (from merge commit or `git diff` against base) against the cached estimate's `files_likely_affected`. Store per-ticket result in `.commander/estimates/accuracy/issue-<N>.json` and roll up per-project precision/recall in `.commander/estimates/accuracy/summary.json`. Surface a warning on preview-dag when recent accuracy falls below a threshold (e.g. <70% file recall over last 10 tickets) — "predictions too unreliable to parallelize safely." Acceptance: after sprint completes, accuracy artifacts exist for merged tickets; preview-dag shows amber banner when threshold breached; no GitHub API calls required.
```

---

## Phase 3 — Fix plan order & split XL from preview-dag 🟢🟡
**Sprint label:** NEW or current planning sprint · **Default labels:** `sprint-planning`, `frontend`
**When:** safe anytime — mostly frontend; does not block sprint-86 run. De-risks Phase 2.

Planning-board workflows triggered from the existing preview-dag / mini-rail data
(`GET /api/sprints/{label}/preview-dag` — levels, conflicts, cycles, unestimated).

```
Add "Apply DAG order" to the sprint planning board. When preview-dag returns ordered levels, show a button on the sprint header (planned / draft sprints only) that rewrites the sprint plan ticket order to match the DAG level order: within each level preserve relative order; across levels follow topological order. Persist via the existing plan-order endpoint / plan.json write path the sprint manager reads at dispatch (`_load_sprint_plan`). Confirm before applying; show a diff summary ("#3 moves before #7 — dep edge #3 blocks #7"). Acceptance: after apply, `_load_sprint_plan` order matches preview-dag levels; drag-drop manual order still works afterward; no-op when preview is partial (unestimated tickets) unless user confirms.
---
Add inline plan-order fixes from preview-dag conflict and cycle hints. On the mini-rail / concurrency preview, when two tickets share files or a cycle is detected, show actionable chips: "Move #B after #A" (for file-overlap or depends_on) and "Remove from sprint" / "Move to backlog". Each action updates plan order or removes the sprint label via existing APIs without a full reload. Re-fetch preview-dag after each fix. Acceptance: clicking "Move #B after #A" updates plan order and the preview refreshes with the conflict gone; cycle warning lists the involved tickets with one-click move suggestions.
---
Flag wrong manual order vs DAG on the board. When the user drag-drops tickets into an order that violates preview-dag levels (a ticket placed before its dependency or ahead of a file-overlap owner), show a row-level warning icon with tooltip explaining the violation and a one-click "Fix order" that moves just that ticket to the earliest valid position. Re-evaluate on every drag-drop without reload. Acceptance: dragging #B before #A when A owns a shared file shows a warning on #B; Fix order moves #B to the earliest slot after A; warning clears when valid.
---
Add "Split ticket" flow from preview-dag and capacity bar. When a ticket is size XL, or preview-dag marks it as blocking parallelism (large file set overlapping many peers), or the sprint capacity bar is red, offer Split on the ticket row. Split opens a dialog: pre-fill two draft titles/AC scopes via a lightweight BA call (or manual edit), create two new GitHub issues, move split children into the same sprint, remove or close the original (user choice), inherit labels minus size-XL. Re-run estimate on both children. Acceptance: splitting #100 produces #101 and #102 on the sprint; preview-dag updates to show them in separate waves when file sets are disjoint; original ticket is removed from the sprint or marked closed with a split reference comment.
---
Surface XL split suggestions before Run Sprint. Extend preflight / Run Sprint confirmation modal: when any sprint ticket is XL or estimated minutes exceed a per-project threshold, list them with "Consider splitting" and link to the Split flow. Block Run Sprint only when user enables a strict gate (off by default). Show rolled-up minutes saved estimate when splits are pending. Acceptance: Run Sprint modal lists XL tickets with split links; default is warn-not-block; strict gate is a project setting defaulting off.
---
Show plan-order and split checklist on preview-dag partial runs. When preview-dag returns `partial: true` (unestimated tickets) or `conflicts` non-empty, render a pre-run checklist at the top of the mini-rail: (1) Estimate N tickets, (2) Resolve K file conflicts, (3) Split M XL tickets — each item links to the existing Estimate all / Apply DAG order / Split actions. Checklist clears item-by-item as fixed; all green enables a subtle "Ready to run" state. Acceptance: unestimated sprint shows checklist with live counts; resolving estimates auto-refreshes preview and checks off items; no runner involvement.
```

---

## Notes

### Phase map vs earlier audit

| Audit lever | Phase |
|-------------|-------|
| Consecutive dup bail (pipeline) | 1 |
| Target paths in coder prompt | 1 |
| Docs/config → Haiku | 1 |
| `depends_on` in runner DAG | 1 |
| BA `## Files to touch` | 1 |
| Warm worktree pool | 2 |
| Multi-coder scheduler | 2 |
| Role-flexible pool | 2 |
| Serialized merge + rebase | 2 |
| Live slot capacity from runner | 2 |
| Multi-lane live view | 2 |
| Prediction accuracy feedback | 2 |
| Apply DAG order / fix conflicts | 3 |
| Split XL from preview | 3 |
| Preflight checklist | 3 |

### Overlap with existing bulk-create batches

- **P3 in [`2026-sprint-runner-enhancements.md`](2026-sprint-runner-enhancements.md)** — concurrency
  preview read-only UI. Phase 3 here adds **actions** (reorder, split, checklist) on top of that preview.
- **R2 in the same file** — superseded for implementation detail by **Phase 2** above (aligned to
  current code: `max_coder_slots`, `#738` merge guard, preview-dag). Do not post both R2 and Phase 2.

### Sprint-85 / sprint-86 safety

| Sprint | Safe to run from this file |
|--------|----------------------------|
| sprint-85 (pending merge) | **Nothing** — merge only |
| sprint-86 (planned) | **Phase 3 only** (frontend planning); enable `pipeline_mode` in settings without new tickets |
| Post-S85 | Phase 1 runner tickets |
| Post-S86 UAT + Phase 3 in use | Phase 2 concurrent engine |

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
