# Commander Shrink — 2026-08

Milestone tracking for the reduction of Commander to a **status + planning web
surface** with **manual Claude Code dispatch**, plus handover of the
knowledge/digest layer to `lookout`.

Status: **planned, not started**
Owner: operator (manual Claude Code dispatch)
Source: 7-agent audit, 2026-08-12

---

## Decisions taken

| # | Decision | Rationale |
|---|---|---|
| 1 | **Cut the launcher** (Option B) | The dashboard never imports `sprint_manager.py` — it is a subprocess path only (`startup.py:2103`, `sprint_run.py:360`, `sprint_dispatch.py:346`). Board / History / Bulk create / Settings / planning read GitHub mirror + SQLite and are unaffected. Reversible via git. |
| 2 | **Keep Finish sprint in the UI** | `sprint_finish.py` imports nothing from `services/sprint_manager`; survives the cut intact. |
| 3 | **Keep the Deploy tab** | `environments.py` + `deploy_actions/config_schema/render_actions/validation` retained as operator tooling. |
| 4 | **Keep the Running view, re-feed it** | `agent_runs` is written only at `sprint_manager.py:741,796`, so the Running tab is *already blind* to manual sessions. Re-feed from the existing hooks (~200 LOC) instead of maintaining ~20k LOC of orchestration. |
| 5 | **Cut B stands as audited** | Hand the knowledge/digest layer to lookout, gated on lookout actually gathering commander first. |
| 6 | **Restore dispatch and rerun as endpoints** (2026-08-19, #2314) | Partially reverses decision 1 for *triggering only* — see the section below. |
| 7 | **Restore sprint-branch model** (2026-08-20, #2329) | feature → sprint/sprint-N → develop replaces feature → develop. Sprint arrives as one reviewable PR. Child sprint labels stay banned. See the section below. |

## Decision — dispatch triggering (2026-08-19, #2314)

**Operator decision: option 1 — server-side dispatch and rerun endpoints.**

`POST /api/sprints/{label}/dispatch` (#2315) and
`POST /api/sprints/{label}/rerun` (#2318) in the dashboard. Dispatch runs the
per-ticket coder → tester loop server-side; rerun resets failed tickets in a
sprint back to a dispatchable state.

### Scope of the reversal

This **supersedes the #2311 reasoning** that "dispatch is a CLI activity now; a
server endpoint is the wrong home for it" — **for triggering only.** Everything
else from the shrink stands: the autonomous orchestrator, its gate pipeline, its
fix-loop are **not** restored. What lands is a trigger and a queue consumer,
not a scheduler with opinions. (The sprint-branch/PR shape is restored
separately — see the 2026-08-20 entry below.)

### Why the reversal

The #2311 decision assumed an operator at the keyboard. Both Commander agent
entry points require `--dangerously-skip-permissions`, and an assistant session
is blocked from spawning a permission-elevated agent. Verified 2026-08-19:

| Action | Result |
|---|---|
| `ssh` to the host | allowed |
| `claude -p "..."` headless, default permissions | allowed |
| `claude -p ... --dangerously-skip-permissions` | blocked by permission classifier |
| `claude -p ... --permission-mode acceptEdits` | blocked by permission classifier |

So "dispatch is a CLI activity" resolved in practice to "the operator personally
runs every dispatch and every retry, for every ticket" — two invocations per
ticket, ten for a five-ticket sprint. That cost was not priced into the original
decision.

`scripts/retry_ticket.py` is **not** replaced. It stays the CLI path, and the
endpoint wraps the same functions so the two cannot drift.

### Constraints carried forward verbatim from #2311

- **Must not mint child sprint labels.** The old rerun created `sprint-N.1/.2/.3`,
  fragmenting one logical sprint across four labels and breaking sign-off and PR
  flows.
- **Must not reorder tickets.** The old rerun reversed order and once queued a
  delete-the-tests ticket ahead of the deletions it covered.
- **Must not write sprint lifecycle state.** A cancelled sprint stuck at
  `needs_rework` and same-label re-dispatch 409'd forever; reconcile would not
  clear it.

All three are asserted by tests in #2315 and #2318, on the AST rather than on
source text.

### On ticket failure

Dispatch **stops**. It does not continue into dependent tickets, and it records
which ticket failed. Recovery is a separate, explicit call — resetting and
running are deliberately not merged, so a reset can be inspected before anything
executes.

### Sequencing

#2316 (baseline-delta check) lands **before** #2315. Push-button dispatch that
merges to develop with no objective check behind it is the combination worth
avoiding: the gate pipeline is gone, and the tester agent merges on its own
say-so.

---

## Decision — sprint-branch model (2026-08-20, #2329)

**Operator decision: restore the sprint-branch model.**

Reverses the "sprint-branch/PR shape is not restored" clause from the #2314
decision.  The dependency problem was concrete: run `9fb3d8770bf2` on
viral-radar sprint-7 had to stop after ticket #81 because ticket #82 depended
on both #80 and #81, neither of which had merged to develop yet.  With a sprint
branch, #82 branches from `sprint/sprint-7` where its dependencies already sat.

### What is restored (2026-08-20)

- `sprint/sprint-N` is created from develop when a sprint dispatch starts.
- Feature branches are cut from `sprint/sprint-N` (auto-detected via issue labels).
- Tester merges feature branches into `sprint/sprint-N`, not develop.
- When all tickets succeed, the dispatch runner opens one PR from
  `sprint/sprint-N` into develop.
- The baseline-delta check (#2316) gates both merge types: sprint branch
  for per-ticket merges, develop for the final sprint merge.

### What is NOT restored

- The autonomous orchestrator, its gate pipeline, its fix-loop.
- **Child sprint labels** (`sprint-N.1/.2/.3`) — explicitly still banned.
  A sprint *branch* is not a sprint *label*; this restores only the former.

### Additive and detected, never assumed

`scripts/start_feature.py` and `scripts/finish_feature.py` detect the sprint
branch by reading the issue's labels at runtime.  When no sprint branch exists
for an issue (projects that do not use the model), both scripts fall back to
develop.  The sprint-branch model is therefore **opt-in per sprint**, enforced
by whether the branch exists on the remote.

---

## Invariants — must not break

- **Lookout read contract.** `lookout/gather.py` reads exactly `GET /api/health`,
  `GET /api/projects/{slug}/brief`, `GET /api/sprints/history` over HTTP.
  `brief_service.py` and those three endpoints are **permanent keeps**.
  Guarded by ticket S4-7.
- **`state_machine.py` is core**, imported by `apps/dashboard/db.py` and
  `github_client.py`. Not to be confused with `state.py` (cut).
- **`agent_browser_runner.py` is used by the manual tester**
  (`.claude/agents/tester.md:462`). Keep.
- **`model_routing.py` is shared** with bulk-create / split-XL / conflict paths.
  Split, do not delete.
- **Do not delete** `apps/dashboard/commander.db.corrupt-20260731*` — preserved
  artifact for open issue #2037.

---

## Sprint 1 — Bugs and zero-risk deletions

Nothing here depends on any other ticket. Ship first.

### S1-1 — Manual `/tester` never transitions ticket to UAT — **S**
`scripts/finish_feature.py:238` prints `FINISH_FEATURE_OUTCOME`, which has zero
consumers in the repo. The only code that flips a ticket to UAT is
`sprint_manager.py:2033,2093`, reachable solely from the autonomous loop. Every
hand-tested ticket is therefore stuck on `sit` and never appears as awaiting
sign-off. `.claude/agents/tester.md:579` states the opposite and is wrong.

AC:
- `finish_feature.py` calls `state_machine.transition(..., UAT)` after a
  successful merge, guarded so the dispatch path does not double-transition
  (e.g. skip when `COMMANDER_SPRINT_LABEL` is set).
- `.claude/agents/tester.md:579` corrected.
- Behavioral test: run finish against a fixture issue with the env var unset,
  assert the label moved; with it set, assert it did not.

Files: `scripts/finish_feature.py`, `services/sprint_manager/state_machine.py`,
`.claude/agents/tester.md`

### S1-2 — pytest writes through to the production todo store — **S**
`.commander/project_todos_store.json` holds 77 rows; 72 are test fixtures
(`p`, `my-project`, `other`, `test-todos-validate-872`). Only 5 are real, all
perf-coach. Tests share the live JSON path with the running dashboard.

AC: a conftest fixture points `todo_repo`'s store path at `tmp_path` for the
whole suite; the production file is purged of fixture-named projects; a test
asserts the store path is not the real one during a test run.

Files: `conftest.py`, `services/sprint_manager/todo_repo.py:96`

> Note: Todos is deleted outright in Sprint 4 (S4-1). Do this anyway — it is a
> live data-corruption bug and Sprint 4 is gated on lookout work.

### S1-3 — Export agent role/issue env vars for manual sessions — **S**
Hooks already fire for hand-driven sessions (`.claude/settings.json`, matcher
`.*`, no sprint_manager dependency), but `CLAUDE_AGENT_ROLE` /
`CLAUDE_AGENT_ISSUE` are set only by `dispatch.py:615-617`, so manual telemetry
lands unattributed.

AC: `.claude/agents/coder.md` and `tester.md` export both vars at the start of
their workflow; a session's events resolve to the right role + issue in
`routers/logs_service.py:180-208`.

Prereq for: S3-2.

### S1-4 — Delete the dead Logs/Activity code — **M**
Issue #2025 removed the Logs tab; `project.html:11435-11436` documents the dead
branches as removed. But `evlFetch()` is still called at `project.html:13154` on
every page load and writes to `#logs-nav-badge`, which no longer exists in the
markup. `_logsUpdateNavBadge` always no-ops.

AC: remove `evlFetch/evlRender/evlRenderTimeline` and the Events-Activity-Log
and error-badge blocks (~`project.html:26410-27900`), plus `src/logpanel.js`,
`src/logs-error-badge.js`, `src/logs-view-controls.js`,
`src/activity-grouping.js`; rebuild the bundle; no console errors on load.

~1,750 LOC + 4 modules.

### S1-5 — Delete orphans: `advisor.py` + zero-caller endpoints — **S**
`services/sprint_manager/advisor.py` (147 LOC) was left behind when #2075 deleted
its routers; zero references repo-wide. Six endpoints have zero frontend callers:
`sprint_preflight` `cycle-check` / `conflicts` / `dep-order`,
`sprint_finish` `conflict-status`, `sprint_dispatch` `POST /api/sprint-run`
(superseded by `/api/sprints/run`), `deploy.py` `POST /api/deploy/promote`.

AC: all removed; `GET /api/board` and `GET /api/sprint-management/issues` in
`sprint_dispatch.py` remain live.

### S1-6 — `.gitignore` fix + reclaim ~300MB — **S**
`.gitignore` has `*.db`, `*.log`, `*.bak`, but the files on disk are
`commander.db.bak-20260621-182552`, `prd.log.1`, etc. — the globs never match,
so they sit untracked forever.

AC: patterns widened to `*.db*`, `*.log.*`, `*.bak-*`, `*.corrupt-*`; deleted:
root `commander.db` (11MB, corrupted, unreferenced), `dashboard.db` (0B, both
copies), `commander.db.bak-*` (150MB, superseded by `.commander/db-backups/`),
`prd.log.1-5` + `uat.log` + `uat_server.log` (~139MB).
**Not deleted:** `commander.db.corrupt-20260731*` (issue #2037).
Verify one restore from `.commander/db-backups/` before deleting the `.bak` files.

### S1-7 — Archive completed one-off migration scripts — **S**
Eight scripts verified complete against live data:
`migrate_repo_structure.py`, `rollback_repo_structure.sh`,
`migrate_to_separate_dbs.py` (`dashboard.db` is 0B), `repair_sprint_collisions.py`
(hardcoded to the sprint-66 incident), `audit_sprint_terminal_state_drift.py`,
`export_to_neon.py`, `migrate_sprints_to_neon.py` (Neon disabled),
`backfill_sprint_project.py` (`sprints.project` has 0 empty rows).

AC: moved to `scripts/archive/`; `scripts/AGENTS.md` index updated so
`test_2057__scripts_agents_md_coverage.py` stays green.

~1,900 LOC.

Open question, not part of this ticket: `backfill_agent_runs_project.py` is
**not** done — `agent_runs.project` is empty on 26,252/26,460 rows (99%). Decide
whether to run it or accept the historical gap.

---

## Sprint 2 — Cut A, autorun peripherals

No UI surface the operator named is touched. S2-1 must precede S2-2.

### S2-1 — Decouple `summary.py` from cut modules — **M**
`summary.py:25-38` imports `alerts.dispatch_alerts`, `state.SprintState`, and
five names from `timekeeping` — all slated for deletion — while `summary.py`
itself produces the sprint-summary artifact the dashboard renders.

AC: `summary.py` no longer imports `alerts`, `state`, or `timekeeping`; the
handful of helpers actually used (`SPRINTS_DIR`, Bangkok-tz conversion, token
window totals) are inlined or moved to `paths.py`; keeps its imports of
`paths`, `retro`, and `agent_browser_runner`; existing summary tests pass.

### S2-2 — Remove the scheduler — **M**
Overnight auto-dispatch queue. `routers/scheduler.py` (83) +
`scheduler_service.py` (221) + `services/sprint_manager/sprint_scheduler.py`
(304) + the `scheduled-run.js` toggle in the board UI.

AC: routes gone, UI toggle gone, `scheduler_service.py:110`'s use of
`list_backlog_issues` disappears with it, bundle rebuilt.

### S2-3 — Remove post-sprint auto-agents — **M**
`post_sprint.py` (1,326) + `document_issue.py` (682) +
`scripts/run_post_sprint.py`. Superseded by running `/rev`, `/est`, and the
`documenter` agent by hand.

AC: modules deleted; the `documenter` agent definition and `/rev` `/est`
commands remain functional standalone.

### S2-4 — Remove AI branch-conflict resolution — **M**
`routers/resolve_conflict.py` (82) + `resolve_conflict_service.py` (196) + the
`/resolve-conflict-stream/` EventSource at `bulk-complete-modal.js:384`.

**Surgical**: `bulk-complete-modal.js` is part of the Finish wizard, which is
being kept. Remove only the AI-resolve path; the wizard must still work, falling
back to reporting the conflict for manual resolution.

### S2-5 — Remove autorun babysitting modules — **L**
`alerts.py` (291), `dead_letter_escalation.py` (222), `brief_generator.py` (287),
`label_transitions.py` (341), `events.py` (169), `ica_preflight.py` (81),
`api_client.py` (54), `services/sprint_manager/failures.py` (435 — note: this is
the retry/revert bookkeeping, **unrelated** to `routers/failures_service.py`,
which backs the Failures inbox and stays).

Depends on: S2-1.

### S2-6 — Remove dispatch glue — **S**
`sprint_webhook_service.py` (522), `dispatch_service.py` (68),
`routers/deploy.py` (64, zero callers — distinct from the Deploy tab, which is
`environments.py` and stays).

### S2-7 — Remove standalone live-run viewers — **M**
`static/run_browser.html` (1,194, ntfy deep-link target) + `rerun-modal.js` (268).

AC: any ntfy notification template that links to `/run-browser` is updated or
removed so the link does not 404.

### S2-8 — Delete tests covering S2-2..S2-7 — **M**

---

## Sprint 3 — Cut A, launcher removal

The destructive sprint. One branch, revertible as a unit. Order matters.

### S3-1 — Extract `list_backlog_issues` — **S**
`routers/sprint_run.py:461` and `scheduler_service.py:89` import
`sprint_manager` purely to reach `_sm.list_backlog_issues`, which is actually
defined at `pipeline.py:652` and re-exported via `sprint_manager.py:75`. This is
the last live import path into the orchestrator.

AC: function moved to a small planning-only module; both call sites repointed
(one disappears with S2-2); `grep` confirms zero non-subprocess references to
`sprint_manager` from `apps/dashboard/`.

Blocks: S3-5.

### S3-2 — Manual-run recorder — **M**
Replaces the ~20k LOC being deleted with ~200. `agent_runs` rows are currently
INSERTed only at `sprint_manager.py:741,796`, so the Running view is empty for
every hand-driven session.

AC: the existing `Stop` / `PreToolUse` hooks (or a small helper they call) write
an `agent_runs` row keyed by issue + role + session id, with `sprint_label` NULL
when absent; `project` is populated (unlike 99% of historical rows); the write is
best-effort and never fails a session.

Depends on: S1-3.

### S3-3 — Read path tolerates a missing sprint label — **M**
`routers/runs.py:114,163,191` key on `{sprint}/{issue}/{agent}`; `sprint_live.py`
reads `.commander/sprints/sprint-N-state.json`. Manual sessions have neither.

AC: run log + reasoning views resolve by issue + role + session when
`sprint_label` is NULL; the Running view renders live manual sessions;
`sprint_finish.py:315`'s `agent_runs_for_sprint` still returns rows for a sprint
assembled from manual runs.

Depends on: S3-2.

### S3-4 — Attach sprint-summary generation to Finish — **M**
`summary.py`'s only caller today is the launcher. Finish is being kept, so the
summary must be produced there instead or it silently stops being generated.

AC: the Finish flow generates the sprint summary artifact; `sprint_run.py`'s
`_read_sprint_summary_url` consumer is repointed or removed; a finished sprint
still shows its summary in History.

Depends on: S2-1.

### S3-5 — Delete the orchestrator — **XL**
`sprint_manager.py` (5,753), `dispatch.py` (1,695), `gates.py` (1,428),
`pipeline.py` (1,210), `worktree.py` (527), `worktree_pool.py` (408),
`concurrent_scheduler.py` (288), `serialization.py` (195), `timekeeping.py`
(209), `state.py` (301), `config.py` (468).

**Do not delete `state_machine.py`** — different file, core dependency.

Depends on: S3-1, S3-4.

### S3-6 — Delete the launch routes and spawn paths — **L**
`routers/sprint_run.py` (1,153) + `sprint_run_service.py` (185),
`finish_progress.py` (171) + `finish_progress_service.py` (326) *(verify: these
stream the Finish run — if Finish still needs progress streaming, keep them and
retarget)*, the spawn + orphan-sweep code in `startup.py:282-360,2103,2894-2931`,
and the `POST /api/sprint-run` path in `sprint_dispatch.py:346`.

**Keep**: `GET /api/board`, `GET /api/sprint-management/issues`,
`GET /api/sprints/{label}/state*` (retargeted by S3-3), the entire Finish and
Deploy surfaces.

### S3-7 — Remove dispatch controls from the UI — **L**
`run-controls.js` (1,457, includes the preflight modal), the Run / Cancel /
Re-run buttons at `board-render.js:1543-1604`, `board-overlay.js` (193, the
blocking "Working…" lock, only invoked by the above), and `runSprintAction()` at
`home.html:1270-1276,1313`.

**Surgical**: `board-render.js:1454-1620` computes the status badge *and* the
action button in one pass — split the function rather than deleting a block.

**Keep**: read-only preflight warnings (unestimated / stale-estimates /
missing-AC), the Finish wizard, the Deploy tab.

### S3-8 — Delete dispatch-pinned tests — **M**
125 files / ~1,223 tests importing `dispatch`, `pipeline`, `sprint_manager`,
`concurrent_scheduler`, `worktree_pool`, `agent_browser_runner`.

Caveat: `agent_browser_runner` stays (manual tester uses it) — keep its tests.

### S3-9 — Post-cut verification — **M**
Manual pass over every surface the operator uses: Bulk create, sprint plan view,
Running view (now fed by S3-2), History, Settings, Finish sprint, Deploy tab.
Plus the lookout contract endpoints (see S4-7).

> Known baseline before starting: the full pytest suite does not finish (hangs
> past 10 min); the working substitute is the 21-file scoped health gate with a
> **25-failure baseline**. `npm test` is broken on Node v26; use
> `node --test tests/frontend/*.test.mjs` (427 tests, **24-failure baseline**).
> Do not interpret either baseline as a regression from this work.
>
> **Correction, 2026-08-20 (#2331, #2338).** Both pytest claims above were
> wrong, and were wrong when written down here. The suite was not hanging — it
> aborted at collection, because three modules imported `_enrich_home_artifact`
> after the shrink removed it, so it reported `0 passed / 0 failed` and the
> ~25-failure figure had nothing to contradict it. Repaired, develop measures
> **2442 failed / 6999 passed / 363 skipped** in ~742s. There is also no
> "scoped" gate: `suite_health_gate.py` runs the full suite. Left in place
> above rather than edited, because what the milestone believed at the time is
> the point of this note.

---

## Sprint 4 — Cut B, lookout handover

**Gated.** Do not start the commander-side deletions until LK-1..LK-3 are done,
or the digest layer is lost with nothing replacing it.

### Lookout-side prerequisites

**LK-1 — Gather commander for real — S.** `vault/projects/commander/` contains
only `.gitkeep`, created so lint would pass (issue #7). Commander is registered
in `targets.yaml` but has never produced a snapshot, `situation.md`,
`capability.md`, `drift.md`, or atlas.

**LK-2 — Wire `bin/lookout <target>` to the full pipeline — M.** It currently
runs gather → lint → commit only. `synthesize.py`, `drift.py`,
`capability_card.py`, `atlas_trace.py`, `question_registry.py`,
`journal_crosslink.py` are manual side-invocations.

**LK-3 — Fix and install the launchd schedule — S.** The plists point at
`/Users/zeal-server/dev/lookout/.commander/runtime/worktree-pool/slot-0/...`, a
transient worktree path, and are not loaded (`launchctl list | grep lookout` is
empty).

Also unresolved on the lookout side, decide before relying on the todo view:
`notion_todos_db` / `notion_digest_page_id` are `<placeholder>`, and `llm.py` is
uncommitted and gated behind an unset `LOOKOUT_LLM=1`, so all generated prose is
deterministic-fallback text.

### S4-1 — Delete Todos — **M**
`routers/todos.py` (217), `todo_repo.py` (401), `todo_attachment_repo.py` (181),
`static/project-todo.js` (721), and the todo dock at `project.html:9792`.
Real usage is 5 rows, all perf-coach, none for commander; last write 2026-07-06.

Replacement: `docs/todo.md` becomes the single source, which lookout's
`todo_view.py` already mirrors verbatim.

### S4-2 — Delete docs-freshness — **S**
`scripts/check_docs_freshness.py` (209), the `docs_freshness_warnings` table
(0 rows, no cron or plist anywhere), and the panel at
`home-preview.html:1386,1437` that polls it.

Replacement: lookout `drift.py`.

### S4-3 — Delete the brief caching / LLM / daily-report layer — **M**
`brief_summary.py` (377), `brief_artifact.py` (266), `brief_invalidation.py`
(26), `scripts/generate_daily_report.py` (455),
`scripts/com.commander.daily-report.plist` +
`install_daily_report_launchd.sh`. `brief_artifacts` last generated
2026-07-06; the launchd job was never installed.

**KEEP `brief_service.py` and `GET /api/projects/{slug}/brief`** — lookout reads
it. See S4-7.

Depends on: S5-1 (the live status view must replace what `home.html:951`'s
`_devReportTick` is polling before its data source is removed).

### S4-4 — Delete the changelog API — **S**
`routers/changelog.py` (27) + `changelog_service.py` (125). Zero frontend
callers; lookout reads the target's `CHANGELOG.md` from disk, not this endpoint.

### S4-5 — Delete dead milestone surfaces — **S**
`home_milestone.py` (26, zero callers — orphaned by the Roadmap/Advisor ADR,
`docs/decisions/2026-08-01-1-delete-roadmap-and-advisor.md`) and the milestone
CRUD endpoints (create/update/close, zero callers).

**Keep** `GET /api/milestones` — the selector at `project.html:12179` uses it.

### S4-6 — Retire the bulk-create prompt library — **S**
`docs/bulk-create/` — 36 files, last committed batch 2026-07-03, plus 3 drafts
never committed. Superseded by lookout's `ideas_ledger.py` + `promote.py`, which
emits bulk-create-style sprint packs with frontmatter validation and
ship-tracking.

**This deletes the saved prompt files, not the Bulk Create UI** (`bulk_tickets.py`
1,311 LOC), which is retained.

Before deleting: `docs/bulk-create/2026-06-21-2-planning-definition-of-ready.md`
is the spec for S5-2 — migrate it first.

### S4-7 — Lock the lookout read contract — **S**
Guard ticket, land it **before** S4-3.

AC: a test asserts `GET /api/health`, `GET /api/projects/{slug}/brief`, and
`GET /api/sprints/history` all return 200 with their current response shape; the
test names lookout as the consumer so a future cut does not silently break it.
Optionally cross-check against `lookout/docs/hermes-contract.md`.

---

## Sprint 5 — The add half

### S5-1 — Live cross-project status view — **L**
`home.html`'s Dev Report is a nightly digest whose generator was never scheduled;
it serves artifacts last generated 2026-07-06 while `_devReportTick`
(`home.html:1375`) keeps polling. This is the operator's primary use of the site.

AC: one screen showing, live, across all projects: sprints running, tickets
awaiting sign-off, blocked items, recent failures — derived from the board /
sprints data path, no nightly generation step.

Blocks: S4-3.

### S5-2 — Definition of Ready — **L**
Spec already drafted and never run:
`docs/bulk-create/2026-06-21-2-planning-definition-of-ready.md` —
`parse_ticket_spec`, a `readiness` block on preflight, and a board gate
(`definition_of_ready_mode = off|warn|block`).

Higher value after Sprint 3: the dispatch-time gates that used to backstop a weak
ticket are gone, so a bad ticket now burns operator time instead of an agent
retry loop.

### S5-3 — Make `diagnostics.html` reachable — **S**
A real system-health page whose only link lives on the effectively-dead
`home-preview.html`. Add a nav entry from the current shell.

### S5-4 — Surface buried planning data — **M**
Backends that already exist and are hard to reach: preflight warnings
(`sprint_preflight.py:425`), dep-order, conflicts, estimate-vs-actual and sprint
outcome (`estimates.py:715,970`), mis-sizing flags. Frontend surfacing only — no
new backend.

---

## Rollup

| Sprint | Tickets | Approx LOC removed | Gated on |
|---|---|---|---|
| 1 — Bugs + zero-risk | 7 | ~3,800 + 300MB | — |
| 2 — Autorun peripherals | 8 | ~6,900 | — |
| 3 — Launcher removal | 9 | ~20,400 + 1,223 tests | S1-3 |
| 4 — Lookout handover | 7 (+3 lookout) | ~3,300 + 36 docs | LK-1..3, S5-1 |
| 5 — Add | 4 | (adds) | — |

Total removal: **~34,000 LOC and ~1,223 tests**, against ~200 LOC added to keep
the Running view working.

## Filed issues

All filed 2026-08-12. Commander: `zealchaiwut/commander`. Lookout: `zealchaiwut/lookout`.

| Plan ref | Issue | Sprint |
|---|---|---|
| S1-1 | #2230 | sprint-1021 |
| S1-2 | #2231 | sprint-1021 |
| S1-3 | #2232 | sprint-1021 |
| S1-4 | #2233 | sprint-1021 |
| S1-5 | #2234 | sprint-1021 |
| S1-6 | #2235 | sprint-1021 |
| S1-7 | #2236 | sprint-1021 |
| S2-1 | #2237 | sprint-1022 |
| S2-2 | #2238 | sprint-1022 |
| S2-3 | #2239 | sprint-1022 |
| S2-4 | #2240 | sprint-1022 |
| S2-5 | #2241 | sprint-1022 |
| S2-6 | #2242 | sprint-1022 |
| S2-7 | #2243 | sprint-1022 |
| S2-8 | #2244 | sprint-1022 |
| S3-1 | #2245 | sprint-1023 |
| S3-2 | #2246 | sprint-1023 |
| S3-3 | #2247 | sprint-1023 |
| S3-4 | #2248 | sprint-1023 |
| S3-5 | #2249 | sprint-1023 |
| S3-6 | #2250 | sprint-1023 |
| S3-7 | #2251 | sprint-1023 |
| S3-8 | #2252 | sprint-1023 |
| S3-9 | #2253 | sprint-1023 |
| S4-7 | #2254 | sprint-1024 |
| S4-1 | #2255 | sprint-1024 |
| S4-2 | #2256 | sprint-1024 |
| S4-3 | #2257 | sprint-1024 |
| S4-4 | #2258 | sprint-1024 |
| S4-5 | #2259 | sprint-1024 |
| S4-6 | #2260 | sprint-1024 |
| S5-1 | #2261 | sprint-1025 |
| S5-2 | #2262 | sprint-1025 |
| S5-3 | #2263 | sprint-1025 |
| S5-4 | #2264 | sprint-1025 |
| LK-1 | lookout#73 | sprint-10 |
| LK-2 | lookout#74 | sprint-10 |
| LK-3 | lookout#75 | sprint-10 |

Cross-repo dependencies: commander #2254, #2255, #2256, #2257, #2260 all gate on
lookout #73/#74 landing first.

## Open questions

1. `backfill_agent_runs_project.py` — run it for the 26,252 historical rows
   missing `project`, or accept the gap? (Zero GitHub quota either way.)
2. Retire the `coder/` and `tester/` clones? 2.3GB, two venvs, two drifting DBs,
   plus `sync_uat.sh` / `copy_to_tmp.sh` existing only to keep them warm. Last
   commits 2026-07-06 and 2026-07-21. Only one live process exists on the
   machine: launchd, `WorkingDirectory=uat/apps/dashboard`. Nothing depends on
   them being warm once the launcher is gone.
3. `project.html` is 31,015 lines — 9,711 inline CSS + a single 16,264-line
   `<script>`. Sprint 3 removes ~4k. Extract the remainder into `static/src/`
   afterwards, or leave the monolith?
4. `finish_progress.py` / `finish_progress_service.py` — confirm during S3-6
   whether the Finish wizard still needs its own progress stream, or whether that
   stream only ever served the autonomous run.
