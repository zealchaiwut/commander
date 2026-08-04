# Changelog

## Sprint 1019

Sprint-lifecycle correctness hardening plus test-isolation and lint follow-ups from the #1746/#2065 rules. **complete-step lineage loop guard (#2172):** the walk-up that retargets a merged-and-pruned parent branch to the next surviving ancestor now tracks visited labels and caps depth at 50; a self-referential or too-deep ancestry chain aborts with `409` instead of spinning forever. **Cross-project parent-label bleed fix (#2170):** every `_sprint_merge_parent_label` call in the finish flow (`complete-step`, `bulk-complete-preview`, `_finish_merge_steps`) now passes `project=`, so a child sprint's merge parent is resolved from the target project's own sprint config rather than whichever project's config the walk landed on. **Periodic DB check off the event loop (#2169):** `_periodic_db_integrity_loop` now dispatches `db.alert_if_corrupt` via `asyncio.to_thread` so the 30-minute `PRAGMA quick_check` no longer blocks the async event loop. **Surface ready-to-merge record failures (#2174):** `_sprint_db_mark_merged_completed` logs a `WARNING` (with the sprint label and exception) instead of a bare `pass` when `record_sprint_ready_to_merge` fails. **Sprint-JSON write isolation (#2166):** a `conftest.py` guard prevents the pytest suite from writing real draft-sprint JSON into a production `.commander/sprints/` directory. **Test-quality follow-ups:** `test_1922__verify_all_ac` now asserts observed behavior instead of an exact source line (#2005); the #1967 cost test adds a call-count spy proving `_query_token_usage` runs once per `_compute_cost` — and the source-side `_compute_cost` was refactored to a single query (#2004); the `1760` live-server module now documents why it needs the `_LIVE_SERVER_TEST_MODULES` guard (#2018); and the stray `apps/dashboard/test.db-wal` was removed from git tracking (#1994).

- [#1994](https://github.com/zealchaiwut/commander/issues/1994) [follow-up] Remove stray apps/dashboard/test.db-wal and gitignore db artifacts — 2026-08-04
- [#2004](https://github.com/zealchaiwut/commander/issues/2004) [follow-up] #1967 test asserts cost output, not the single-query behavior (call-count untested) — 2026-08-04
- [#2005](https://github.com/zealchaiwut/commander/issues/2005) [follow-up] #1922 test_1922__verify_all_ac asserts an exact source line (forbidden #1746 pattern) — 2026-08-04
- [#2018](https://github.com/zealchaiwut/commander/issues/2018) [follow-up] conftest.py adds 1760 module to live-server list beyond #1925 scope — 2026-08-04
- [#2166](https://github.com/zealchaiwut/commander/issues/2166) Full pytest suite can write real draft-sprint JSON files into .commander/sprints/ (test-isolation gap) — 2026-08-04
- [#2169](https://github.com/zealchaiwut/commander/issues/2169) [follow-up] Run periodic DB quick_check off the event loop (asyncio.to_thread) — 2026-08-04
- [#2170](https://github.com/zealchaiwut/commander/issues/2170) [follow-up] Pass project= to _sprint_merge_parent_label from finish flow (cross-project bleed) — 2026-08-04
- [#2172](https://github.com/zealchaiwut/commander/issues/2172) [follow-up] Add cycle/depth guard to complete-step lineage walk-up loop — 2026-08-04
- [#2174](https://github.com/zealchaiwut/commander/issues/2174) [follow-up] Avoid silently swallowing record_sprint_ready_to_merge failure — 2026-08-04

## Sprint 1018

Code-review follow-up cleanups from #1746's AC-testing rules and two frontend CSS fixes. **Behavioral fetch-spy test (#1995):** `test_fetch_spy_harness__1831.py` no longer leans on forbidden source-regex checks — it was rewritten to drive the real code path and assert observed fetch behavior, per the #1746 "AC tests must exercise behavior, not source text" rule. **De-duplicated mobile column-hide rule (#2158):** the failures-table `.fbox-table th/td:nth-child(2)/nth-child(6)` `display: none` mobile rule was declared in two separate `@media (max-width: 600px)` blocks; the duplicate inside the history-card block was removed so the rule lives in one place. **Issues-tab retry button styling (#2159):** the Issues-tab retry button used a `.tkt-retry-btn` class that had no CSS definition; a matching `.tkt-retry-btn` rule (bordered pill with blue hover) was added to `project.html`.

- [#1995](https://github.com/zealchaiwut/commander/issues/1995) [follow-up] test_fetch_spy_harness__1831.py leans on forbidden source-regex checks (#1746) — 2026-08-04
- [#2158](https://github.com/zealchaiwut/commander/issues/2158) [follow-up] Failures-table mobile column-hide rule is duplicated across two @media blocks — 2026-08-04
- [#2159](https://github.com/zealchaiwut/commander/issues/2159) [follow-up] Issues-tab retry button uses .tkt-retry-btn class with no CSS definition — 2026-08-04

## Sprint 1016

Database-durability hardening plus three sprint-lifecycle correctness fixes. **complete-step no longer strands merged-and-pruned lineages (#1934):** when a child sprint's immediate parent branch had been merged and pruned, `_branch_has_unmerged_commits` returned False for the missing base and `complete-step` silently no-op'd while the child's commits stayed stranded; the step now walks up the lineage to the next surviving ancestor branch (or `develop`) and retargets there. It also refuses a base-sprint complete-step with `409` while any child still has a live branch — running or with unmerged commits — and skips closing tickets belonging to such unmerged children. **Runtime integrity_check on disk I/O (#2012):** a `sqlite3.OperationalError('disk I/O error')` caught by the mirror-sync loop's broad `except`-and-continue handlers previously left a corrupt DB to 500-loop indefinitely; a new `db.handle_runtime_disk_io_error()` runs `PRAGMA integrity_check`, logs CRITICAL with the operator restore hint (extracted into a shared `_build_restore_hint()`), and raises to abort the loop — it's a no-op for any non-disk-I/O exception so existing handlers call it unconditionally. **Periodic WAL checkpoint (#2013):** the hourly local-backup tick now drives `run_wal_checkpoint()` after each backup so the WAL file can't grow unboundedly (the function was previously dead code). **bulk-complete-preview 400 for zero children (#2160):** the preview endpoint now returns `400` when the sprint has no child sprints instead of a misleading empty `200`. **Reconcile re-derives terminal state from ticket outcomes (#2167):** an additive ticket-outcome check downgrades a stale `ready_to_merge` → `needs_rework` when stored `issues_json` outcomes contain a failure/dead-letter the GitHub needs-rework-label check misses; it never upgrades `needs_rework` → `ready_to_merge` (that stays the GitHub signal's job). `reconcile-preview` now also returns `outcome_mismatch` and `outcome_derived_state`.

- [#1934](https://github.com/zealchaiwut/commander/issues/1934) [bug] complete-step on a child after its base completed silently strands the child branch — 2026-08-03
- [#2012](https://github.com/zealchaiwut/commander/issues/2012) [follow-up] Run integrity_check on caught runtime disk I/O error (not just at startup) — 2026-08-03
- [#2013](https://github.com/zealchaiwut/commander/issues/2013) [follow-up] Wire run_wal_checkpoint() into a periodic tick or remove it (dead code) — 2026-08-03
- [#2160](https://github.com/zealchaiwut/commander/issues/2160) bulk-complete-preview returns 200 instead of 400 for a sprint with zero children — 2026-08-03
- [#2167](https://github.com/zealchaiwut/commander/issues/2167) reconcile never re-derives sprint terminal state from ticket outcomes — 2026-08-03

## Sprint 1015

Reliability, lineage-correctness, and docs-accuracy fixes. **DB authoritative for sprint lineage (#2048):** `_sprint_merge_parent_label` in `startup.py` now resolves a child sprint's immediate merge parent from the `sprints.immediate_parent` DB column first (ADR-4), falling back to plan.json then the base label with a loud warning; a one-time `_backfill_immediate_parent_labels` heal in `db.py` copies `plan.json.parent` into any child row left NULL, so git merge topology no longer silently rides plan.json. **Periodic DB corruption detection (#2037):** a new `_periodic_db_integrity_loop` runs `PRAGMA quick_check` every 30 minutes (`db.check_db_quick` / `db.alert_if_corrupt`), logging CRITICAL and broadcasting a `db_corruption_alert` event so corruption is caught mid-run instead of sitting undetected until the next restart. **Missing advertised endpoint (#2051):** `GET /api/debug/token-usage/by-agent-model` (advertised in CLAUDE.md but returning 404) is now wired up via `routers/token_usage_debug.py`; also backfilled CHANGELOG/todo for manual sprints viz9001/9002, refreshed `frontend-map.md` + `2.3a`, clarified deliberated-vs-auto-adopted ADRs in `decisions/README.md`, and added `scripts/run_post_sprint.py`. **Stale test fixed (#2035):** `test_1411__worktree_pool.py` rewritten for the shared-venv pool design (was asserting a per-slot venv).

- [#2035](https://github.com/zealchaiwut/commander/issues/2035) Fix stale test_1411__worktree_pool.py — shared-venv pool design — 2026-08-03
- [#2037](https://github.com/zealchaiwut/commander/issues/2037) Periodic PRAGMA quick_check to detect DB corruption mid-run — 2026-08-03
- [#2048](https://github.com/zealchaiwut/commander/issues/2048) Make DB authoritative for sprint immediate_parent lineage — 2026-08-03
- [#2051](https://github.com/zealchaiwut/commander/issues/2051) Expose GET /api/debug/token-usage/by-agent-model + docs backfill — 2026-08-03

## Sprint viz9002 _(manual sprint — backfilled 2026-08-03; documenter did not run)_

ADR capture, Brain search tab, sprint-runner hardening, and three reliability fixes.
**ADR series (#2027):** `scripts/log_decision.py` + `/decide` slash command; thirteen ADRs filed in `docs/decisions/` (Q1–Q3 deliberated; Q4–Q13 auto-adopted provisional recommendations).
**Brain search tab (#2028):** `GET /api/brain/search` (SQLite FTS5) + `GET /api/brain/panels`; new top-nav Brain tab replacing the deleted Metrics/Logs tabs.
**OAuth/auth preflight (#2029):** fail-fast `gh auth status` check at sprint start before dispatching any agents.
**Non-fatal sprint finalization (#2030):** sprint finalization continues on a hard crash rather than leaving tickets in limbo.
**False-orphan sweep fix (#2031):** auto-reconcile sweep no longer flags passing sprints as orphaned (#1887 regression fix).
**Worktree-pool self-heal (#2032):** missing pool slot is rebuilt automatically on next `acquire()`.
**Auto-escalate dead-lettered tickets (#2033):** tickets that fail the dead-letter threshold twice are auto-escalated to the operator.

- [#2026](https://github.com/zealchaiwut/commander/issues/2026) Fill sprint retro Key Learnings + feed into planning — 2026-08-01
- [#2027](https://github.com/zealchaiwut/commander/issues/2027) docs/decisions/ ADR series + /decide capture command — 2026-08-01
- [#2028](https://github.com/zealchaiwut/commander/issues/2028) Brain search: GET /api/brain/search (SQLite FTS5) + Brain tab — 2026-08-02
- [#2029](https://github.com/zealchaiwut/commander/issues/2029) OAuth/auth preflight at sprint start (fail fast) — 2026-08-02
- [#2030](https://github.com/zealchaiwut/commander/issues/2030) Non-fatal sprint finalization on hard crash — 2026-08-02
- [#2031](https://github.com/zealchaiwut/commander/issues/2031) Fix false-orphan sweep flagging passing sprints (#1887) — 2026-08-02
- [#2032](https://github.com/zealchaiwut/commander/issues/2032) Worktree-pool self-heal on missing slot — 2026-08-03
- [#2033](https://github.com/zealchaiwut/commander/issues/2033) Auto-escalate repeat dead-lettered tickets — 2026-08-03

## Sprint viz9001 _(manual sprint — backfilled 2026-07-31; documenter did not run)_

Failures inbox, persistence of agent narratives, reasoning view, dev-report landing, and UI cleanup.
**Unified failures endpoint (#2019):** `GET /api/failures` — normalized failure rows from three SQLite sources (events, agent_runs, agents) behind a single endpoint.
**Failures inbox tab (#2020):** new top-level Failures tab in the dashboard replacing the deleted Logs & Activity tab.
**Persist agent narrative (#2021):** `agent_runs.final_message` now written on dispatch completion; was NULL for all ~26k non-test rows before this sprint.
**Reasoning view (#2022):** `GET /api/runs/{id}/reasoning` + collapsible reasoning panel in the dashboard.
**Dev Report landing (#2023):** auto-refresh live strip on `home.html`; `GET /api/dev-report` now regenerates on `?force=1`.
**Remove dead views (#2024):** deleted dead/orphaned dashboard views and their backend wiring.
**Remove Analytics + Logs tabs (#2025):** top-nav Logs and Metrics/Analytics tabs deleted; backend routes preserved; legacy deep-links redirect to Failures inbox.

- [#2019](https://github.com/zealchaiwut/commander/issues/2019) Unified failures endpoint: GET /api/failures — 2026-07-31
- [#2020](https://github.com/zealchaiwut/commander/issues/2020) Failures inbox: top-level dashboard tab — 2026-07-31
- [#2021](https://github.com/zealchaiwut/commander/issues/2021) Persist agent final narrative to agent_runs — 2026-07-31
- [#2022](https://github.com/zealchaiwut/commander/issues/2022) Reasoning view: GET /api/runs/{id}/reasoning + panel — 2026-07-31
- [#2023](https://github.com/zealchaiwut/commander/issues/2023) Dev Report landing: live auto-refresh strip — 2026-07-31
- [#2024](https://github.com/zealchaiwut/commander/issues/2024) Remove dead/orphaned dashboard views + wiring — 2026-07-31
- [#2025](https://github.com/zealchaiwut/commander/issues/2025) Remove Analytics + Logs top-nav tabs (keep routes) — 2026-07-31

## Sprint 1014

Two correctness fixes to sprint-count reconciliation and cross-project docs isolation. **Unified settled_done formula (#2049):** reconcile recomputed a sprint's denormalized counts (`settled_done`, `uat_count`, `failure_count`) with its own inline formula that disagreed with the one `sprint_artifact_service` uses at materialize time — so a fully-settled sprint could flip from 10/10 to 0/10 after a reconcile pass. `_reconcile_counts` in `sprint_reconcile_service.py` now delegates to the canonical `_compute_summary_counts`, and the dead `_settled_done_from_columns` helper was removed from `startup.py`; the nav pill's "N done" chip is relabeled "N settled" to match. **Brain cross-project docs bleed (#2052):** `resolve_clone_root` in `docs_service.py` walked `main`→`prd` before falling back to `uat`, so the Brain/docs endpoints could serve a stale or wrong clone's docs. Nested-layout resolution now prefers `uat` (the develop-tracking clone with the most current docs) first, in explicit `uat`→`main`→`prd` order, so each project's docs come from its own develop branch.

- [#2049](https://github.com/zealchaiwut/commander/issues/2049) Reconcile can flip a sprint from 10/10 to 0/10: two settled_done formulas write one column — 2026-08-03
- [#2052](https://github.com/zealchaiwut/commander/issues/2052) Brain tab shows Commander's docs for every other project (silent cross-project bleed) — 2026-08-03

## Sprint 1013

A batch of dashboard UX fixes plus a sprint-runner scope-isolation follow-up. **Issues tab now refetches on every visit (#2072):** `switchTab` gated `loadTickets()` behind a one-shot `_ticketsLoaded` flag, so the Issues tab loaded once and then silently showed stale data for the rest of the session. The flag was removed — the tab now calls `loadTickets()` on every switch — and the three `loadTickets` failure states (project-info fetch error, project-not-found, tickets fetch error) each gained an inline **Retry** button so a transient failure is recoverable without a full page reload. **Failures table keeps the diagnostic columns on phones (#2073):** the `≤600px` media query hid columns 3 (Agent) and 5 (Reason) — exactly the two that carry the diagnosis — while keeping the less useful Sprint and Time columns. It now hides Sprint (col 2) and Time (col 6) instead, so Agent and Reason stay visible on mobile. **delete-sprint no longer leaves empty draft rows (#2076):** `DELETE /api/sprints/{sprint_label}` removed the label and on-disk sprint files but left the live `sprints` table row intact, so a later `reconcile-preview` reported the deleted sprint as an empty draft. The endpoint now calls `transition_sprint_state(sprint_label, "deleted", …)` so reconcile reports `exists=false`. **Scope-contamination diff base honors the sprint branch (#2155):** `_feature_branch_diff_files` in `services/sprint_manager/sprint_manager.py` always resolved the merge-base against `origin/develop`/`develop`/`master`, so in sprint mode a feature branch's "own diff" set was polluted by sibling sprint tickets' already-merged files. When `COMMANDER_MERGE_TARGET` names a non-develop branch, that sprint branch (and its `origin/` form) is now prepended to the merge-base candidate list, isolating each ticket's true changes.

- [#2072](https://github.com/zealchaiwut/commander/issues/2072) Issues tab loads once and never refreshes — silently shows stale data — 2026-08-03
- [#2073](https://github.com/zealchaiwut/commander/issues/2073) Failures table hides Agent and Reason on phones — the two columns that carry the diagnosis — 2026-08-03
- [#2076](https://github.com/zealchaiwut/commander/issues/2076) [bug] delete-sprint endpoint leaves empty draft rows in live sprints table — 2026-08-03
- [#2155](https://github.com/zealchaiwut/commander/issues/2155) [follow-up] Scope-contamination diff base ignores the sprint branch in sprint mode — 2026-08-03

## Sprint 1012.1

Three code-review follow-ups hardening the sprint-runner gate and merge-verification paths from the #2142/#2086 reviews. **Narrowed broad except (#2147):** `_gate_failure_scope_contaminated` in `services/sprint_manager/sprint_manager.py` caught `(OSError, json.JSONDecodeError, Exception)` — the trailing `Exception` swallowed every error including programming bugs; it now catches `(OSError, json.JSONDecodeError, ValueError)` only. **Scope-contamination now catches non-test source files (#2148):** the #2142 dead-letter skip only recognised foreign-ticket leakage via `tests/…__<M>.py` filename patterns, so a leaked non-test source file (e.g. `advisor.py`, `roadmap.py`) still triggered wrongful dead-letter escalation. A new `_feature_branch_diff_files` helper computes the files the current feature branch actually changed (merge-base → HEAD, trying `origin/develop`/`develop`/`origin/master`/`master` in order), and `_gate_failure_scope_contaminated` now cross-references any non-test `.py` path in the failure sidecar against that set via a prefix-tolerant `_path_in_diff`; a failure whose file references are *all* foreign skips the dead-letter counter. When the diff set can't be determined the source-file check is skipped and it falls back to the original test-file-only detection. **Observable merge-verification skip (#2151):** `_merge_sprint_branches_for_label` in `apps/dashboard/startup.py` silently skipped the merge-reachability check when a branch tip SHA was unavailable; it now emits a `[merge-verify]` warning naming the head → base branches so the skipped verification is visible in the logs.

- [#2147](https://github.com/zealchaiwut/commander/issues/2147) [follow-up] Narrow broad except (…, Exception) in _gate_failure_scope_contaminated — 2026-08-03
- [#2148](https://github.com/zealchaiwut/commander/issues/2148) [follow-up] Scope-contamination fallback only detects test-file leakage, misses non-test files — 2026-08-03
- [#2151](https://github.com/zealchaiwut/commander/issues/2151) [follow-up] Merge-verification silently skipped when branch tip SHA fetch fails — 2026-08-03

## Sprint 1011.2

Two test-safety and hygiene follow-ups from the #2074/#2075 reviews. **Broadened GitHub prod-write guard (#2141):** the `_gh_no_prod_write_guard` session fixture in `tests/conftest.py` previously wrapped only the module-level `httpx.post` / `httpx.patch` / `httpx.delete` functions, so a write issued via `httpx.Client(...).post()`, `httpx.request(...)`, or a `gh api` subprocess could silently bypass it and hit `zealchaiwut/commander`. The guard now also wraps `httpx.Client.send` (covers any client-instance write) and `subprocess.run` / `subprocess.Popen` (blocks `gh api -X POST|PATCH|DELETE|PUT` calls whose path contains `repos/zealchaiwut/commander/` before the process spawns — so no quota is spent and no mutation occurs). The trailing slash in the match prevents `commander-test` from tripping the `commander` guard. Separately, `_get_coder_test_allowlist` (`services/sprint_manager/gates.py`) now reads `apps/dashboard/.env` directly and merges it with `os.environ`, so additions to `CODER_TEST_PATH_ALLOWLIST` take effect on an already-running dashboard without a restart (`load_dotenv(override=False)` cannot update an already-set env var on a long-lived process). **Stale-comment cleanup (#2140):** the tab-switch feature-flag comment in `project.html` still listed `advisor` as a surface after Advisor was fully deleted in #2075; the reference was removed.

- [#2140](https://github.com/zealchaiwut/commander/issues/2140) [follow-up] Remove stale advisor reference in project.html tab-switch comment — 2026-08-03
- [#2141](https://github.com/zealchaiwut/commander/issues/2141) [follow-up] Broaden GitHub prod-write guard to cover httpx.Client and gh subprocess — 2026-08-03

## Sprint 1011.1

Sprint-runner reliability plus a batch of UAT follow-up fixes. **Overnight dispatch no longer hard-crashes (#2081):** the worktree pool bound itself to `sprint_branch`, but `mode=overnight` explicitly sets `--target-branch develop` and skips the sprint branch, so every `git worktree add` failed and the first `acquire()` blocked for the full 1800s `ACQUIRE_TIMEOUT_SECONDS` before dying with a misleading timeout. The pool now binds to `target_branch` (the branch that actually exists), `WorktreePool.create()` returns the count actually created, and a new `_assert_pool_has_slots` fails fast with an actionable message when 0/K slots are built. **Quality-gate file scope isolation (#2142):** the gate helpers diffed `git diff <base>` (two-dot) against the current sprint-branch tip, so an earlier ticket's already-merged files leaked into a later ticket's gate scope — causing false lint/test failures and wrongful dead-letter escalation. All four helpers (`_changed_py_files`, `_changed_js_ts_files`, `_changed_frontend_files`, `_gate_coder_no_test_edits`) now use the three-dot `<base>...HEAD` range (merge-base → HEAD) to isolate a feature branch's own changes, and `_gate_failure_scope_contaminated` skips the dead-letter increment when a failure sidecar references only foreign tickets' `tests/…__<M>.py` files. **Finish-sprint merge safety (#2086):** the finish endpoint could close issues, mark a sprint completed, and delete branches even when the merge silently no-op'd (branch absent without ever having merged) — invisible code loss the bulk-complete repair couldn't detect. `finish` now aborts with `409 merge_failed` (closing nothing) when a merge step fails, `_merge_sprint_branches_for_label` captures each head tip SHA and verifies it is reachable from the target after the merge, and `bulk-complete-preview`'s `merged` flag distinguishes a merged-then-pruned branch (via `_has_merged_pr`) from one deleted without merging. **Project= resolution follow-ups (#2135, #2136):** the duplicated `_split_project` one-liner in `sprint_finish.py` and `finish_progress.py` (which built an invalid `<slug>/<slug>` repo path for bare slugs) was replaced by central `project_resolver.split_project` / `resolve_project_repo`, which look a bare slug up in the tracked project list and honor the #2064 contract. **Cleaner 422 (#2119):** JSON `POST /api/tickets/create` caught bare `Exception` and leaked `str(ValidationError)`; it now catches `ValidationError` specifically and returns field-scoped `Invalid ticket body: <field>: <msg>; …`. **Frontend & tooling follow-ups:** board's Running tab now shows a running rerun chain instead of the lineage collapse swallowing it (#2077); `_smgmtComputeLeadingEmpty` extracted from `project.html` to `board-render.js` for testability, documenting the sub-sprint-zombie skip (#2089); the History ledger renders an empty state instead of a permanent skeleton when `repo` is falsy (#2090); the health-strip's dead "See more →" link (pointing at the removed metrics/failures tab) was removed (#2093); and `resync_issues_mirror.py`'s summary reads the `synced` key instead of the non-existent `total`, so it no longer always prints "0 rows" (#2085).

- [#2077](https://github.com/zealchaiwut/commander/issues/2077) [bug] running rerun sub-sprint invisible on board's Running tab (lineage chain swallows it) — 2026-08-02
- [#2081](https://github.com/zealchaiwut/commander/issues/2081) [bug] mode=overnight sprint dispatch deterministically hard-crashes after 30min worktree-pool timeout — 2026-08-02
- [#2085](https://github.com/zealchaiwut/commander/issues/2085) [follow-up] resync_issues_mirror.py summary always prints '0 rows changed' (wrong dict key) — 2026-08-02
- [#2086](https://github.com/zealchaiwut/commander/issues/2086) [bug] finish endpoint can close/complete/delete-branch without the merge actually landing (silent code loss, invisible to bulk-complete repair) — 2026-08-02
- [#2089](https://github.com/zealchaiwut/commander/issues/2089) [follow-up] Extend 'Clean up empty sprints' to sub-sprint labels (or document the skip) — 2026-08-02
- [#2090](https://github.com/zealchaiwut/commander/issues/2090) [follow-up] History ledger shows a permanent skeleton when repo is falsy — 2026-08-02
- [#2093](https://github.com/zealchaiwut/commander/issues/2093) [follow-up] Health-strip 'See more →' still lands on failures inbox — repoint/relabel/remove — 2026-08-02
- [#2119](https://github.com/zealchaiwut/commander/issues/2119) [follow-up] JSON create-ticket 422 leaks raw pydantic ValidationError repr — 2026-08-02
- [#2135](https://github.com/zealchaiwut/commander/issues/2135) [follow-up] Deduplicate _split_project helper across sprint_finish.py and finish_progress.py — 2026-08-02
- [#2136](https://github.com/zealchaiwut/commander/issues/2136) [follow-up] Bare-slug project= on canonical sprint finish routes builds an invalid <slug>/<slug> repo — 2026-08-02
- [#2142](https://github.com/zealchaiwut/commander/issues/2142) [bug] quality-gate file scope leaks earlier tickets' changes onto later tickets in the same sprint run, causing false failures + wrongful dead-letter escalation — 2026-08-02

## Sprint 1010.1

Dead-code removal, a test-safety fix, and a landing-page badge fix. **Delete Roadmap and Advisor (#2075):** both features had been left unreachable in half-deprecated limbo — ~1000+ lines across `routers/advisor.py`, `advisor_service.py`, `roadmap.py`, `roadmap_service.py`, `suggestions.py`, and `suggestions_service.py`, plus their frontend (`static/project.html`, `static/home.html`, `shell/features.js`, `shell/tabs.js`) and dead tests. Their endpoints are gone: `/api/roadmap` and `/api/roadmap/*` (milestone create/edit/close/reopen, settings), and the full `/api/projects/{project}/advisor/*` surface plus `/api/advisor/tick`. The GitHub-backed **Milestones** API (`/api/milestones`, `/api/projects/{slug}/milestones`, `/api/home/milestone`) is unaffected and remains. `docs/features/api.md` updated (see `docs/decisions/2026-08-01-1-delete-roadmap-and-advisor.md`). **AC tests no longer write to production GitHub (#2074):** acceptance-criteria tests were creating real milestones in the live repo. Tests now stub/guard the GitHub milestone-write path so a green run leaves the production repo untouched (`tests/test_2074__no_prod_gh_writes.py`). **Landing-page dev-report badge (#2071):** the "live · HH:MM" badge sat over a dev report that could 404 and never regenerate; the badge now reflects the report's `generated_at`, and a fetch failure after a first successful load shows a dismissible error banner instead of blanking the pane (`static/home.html`).

- [#2071](https://github.com/zealchaiwut/commander/issues/2071) Landing page shows a 'live · HH:MM' badge over a dev report that 404s and never regenerates — 2026-08-01
- [#2074](https://github.com/zealchaiwut/commander/issues/2074) Acceptance-criteria tests are creating real milestones in the production GitHub repo — 2026-08-01
- [#2075](https://github.com/zealchaiwut/commander/issues/2075) Decide or delete: Roadmap and Advisor are unreachable, ~1000+ lines in half-deprecated limbo — 2026-08-01

## Sprint 1009.2

Two more API/data-consistency tickets from the seven-reviewer UX audit. **One UTC timestamp format (#2067):** "a UTC instant" was serialized three incompatible ways in the SQLite layer (`datetime.utcnow().isoformat()` with microseconds, bare `strftime("%Y-%m-%dT%H:%M:%S")` with no marker, and `...Z`), so no single `strptime` could parse them. A shared `db.utc_now()` helper now emits the canonical `YYYY-MM-DDTHH:MM:SSZ` (explicit UTC marker, no microseconds, string-sortable) and every write site in `apps/dashboard/db.py` uses it; a companion `db.parse_utc_ts()` reader tolerates all three legacy shapes plus the new `Z` form (historical rows are **not** rewritten). The frontend Failures table's `new Date(ts)` misparse — bare ISO strings are parsed as *local* time per the ES spec, shifting displayed times by the browser offset (7h in Asia/Bangkok) — is fixed by a `_normalizeTs()` helper in `apps/dashboard/static/src/failures/failures.js` that appends `Z` to offset-less strings before parsing. **Consistent error body shape (#2068):** `GET /api/dev-report` returned `404 {"error": …}` while the rest of the API uses FastAPI's `HTTPException` `{"detail": …}`, so `detail`-based clients silently missed the message. It now raises `HTTPException(404, detail=…)`; a router sweep also normalised the delete-sprint conflict path in `sprint_crud.py` from `409 {"error":…, "suggestion":…}` to `409 {"detail":…}`. Deliberate non-error sentinels (`{"found": false}` / `{"exists": false}`) were left intact.

- [#2067](https://github.com/zealchaiwut/commander/issues/2067) One UTC timestamp format — three serializations no single strptime can parse — 2026-08-01
- [#2068](https://github.com/zealchaiwut/commander/issues/2068) Error bodies: dev_report returns an 'error' key while the rest of the API returns 'detail' — 2026-08-01

## Sprint 1009.1

Follow-up re-run of two Sprint 1009 API-consistency tickets. **reconcile-preview 404 for unknown projects (#2069):** `GET /api/sprints/{label}/reconcile-preview` and `POST /api/sprints/{label}/reconcile` previously could not distinguish "sprint not started" from a mistyped `project=`. Both now resolve `project=` through the central `project_resolver.resolve_project_path` before doing any work, so an unrecognised project raises `HTTPException(404)` (`Project '<x>' not found`) instead of returning a misleading `200 exists:false` — consistent with the #2064 contract that resolution failure never falls back to a default. **JSON body on POST /api/tickets/create (#2070):** the endpoint took only `multipart/form-data` while its sibling `/api/tickets/draft` and the rest of the write API take JSON. It now dispatches on `Content-Type`: `application/json` is validated against a typed `CreateTicketBody` (`draft_id, title, body, project, sprint_label, extra_labels, milestone`), while `multipart/form-data` keeps the `files[]` attachment path — both share the same business logic and `TicketCreateResponse` (`201 {number, url}`). `docs/features/api.md` updated for both.

- [#2069](https://github.com/zealchaiwut/commander/issues/2069) reconcile-preview cannot distinguish 'sprint not started' from 'you mistyped the project' — 2026-08-01
- [#2070](https://github.com/zealchaiwut/commander/issues/2070) POST /api/tickets/create takes multipart form data while its sibling takes JSON — 2026-08-01

## Sprint 1009

API-consistency hardening — three tickets unifying how endpoints address projects, sprints, and response types. **One `project=` contract (#2064):** slug vs `owner/repo` was handled ad-hoc per endpoint (each router carried its own copy-pasted `_project_root_path` slug-extraction one-liner), which had already caused a live bug. A new `apps/dashboard/project_resolver.resolve_project_path` is the single normaliser: canonical input is `owner/repo` (what `/api/home` publishes), the bare slug is accepted for backwards compatibility, empty input raises `400`, and an unknown project raises `404` — resolution **never** silently falls back to a default project. `home_service`, `analytics`, `projects`, and `brain` were switched to import it and their local helpers deleted; the base directory is now overridable via `COMMANDER_PROJECTS_BASE`. **Unify the sprint resource path (#2065):** sprint-completion routes existed only under the nested `/api/projects/{owner}/{repo_name}/sprints/{label}/…` scheme while every other sprint route is flat. Canonical flat routes were added — `finish-preview`, `finish`, `finish-bg`, `finish-stream`, `bulk-complete-preview`, `bulk-complete`, `complete-step`, `conflict-status` — each addressed `/api/sprints/{sprint_label}/…?project=owner/repo`; the old nested paths remain as `deprecated: true` aliases that delegate to the same handlers, so existing callers keep working. **response_model on the Hermes-critical endpoints (#2066):** 259 of 279 operations were untyped. Seven endpoints Hermes depends on now carry explicit Pydantic response models (defined in `apps/dashboard/routers/hermes_models.py`): `POST /api/sprints/run`, `GET /api/sprints/{sprint_label}/live`, `GET /api/dev-report`, `POST /api/tickets/draft`, `POST /api/tickets/create`, `GET /api/failures`, `GET /api/board` — so their shapes now appear in `/openapi.json`; models that wrap independently-evolving inner dicts use `extra="allow"`/`Any` with inline justification. `docs/features/api.md` updated for all three.

- [#2064](https://github.com/zealchaiwut/commander/issues/2064) Define and enforce one `project=` contract — slug vs owner/repo is inconsistent and already caused a live bug — 2026-08-01
- [#2065](https://github.com/zealchaiwut/commander/issues/2065) Unify the sprint resource path — two structurally different addressing schemes for one concept — 2026-08-01
- [#2066](https://github.com/zealchaiwut/commander/issues/2066) Add response_model to the Hermes-critical endpoints — 259 of 279 operations are untyped — 2026-08-01

## Sprint 1008.1

Follow-up re-run of the two Sprint 1008 tickets that failed their gates. **Consolidate the sprint-label regex (#2059):** the pattern `^sprint-\d+(\.\d+)?$` was copy-pasted in ~21 places across 3 subtly different shapes (`(\.\d+)?`, `(?:\.\d+)?`, `(?:\.\d+)*`), a drift hazard. A new single-source-of-truth module `apps/dashboard/sprint_label_re.py` exports `SPRINT_LABEL_RE` (compiled full label, base or child), `SPRINT_LABEL_PATTERN` (the raw pattern string, for embedding in error messages), and `SPRINT_BASE_LABEL_RE` (base-only `sprint-N`). Every router and helper (`board_service`, `estimate_jobs`, `estimates`, `finish_progress`, `metrics`, `mis_sizing`, `signoff_service`, `sprint_artifact_service`, `sprint_dispatch`, `sprint_history`, `sprint_history_service`, `sprint_labels`, `sprint_planning`, `sprint_run`, `sprint_summaries`, `timeline`, plus `startup.py` and `log_source.py`) now imports from it instead of compiling its own copy. Pure refactor — no endpoint, parameter, response-shape, or validation-behavior change, so `docs/features/api.md` and `SCHEMA.md` are unchanged. **Restore the health-strip 'See more →' link (#2063):** the link pointed at the `metrics` tab, which was removed in #2025 (`switchTab` silently coerces `metrics`/`logs`/`status` → `failures`). The fix keeps the coercion (so the link lands on `failures` rather than a blank pane) and adds a `console.warn` in `switchTab` so any remaining stale internal callers targeting a removed tab are visible in development.

- [#2059](https://github.com/zealchaiwut/commander/issues/2059) Consolidate the sprint-label regex — 21 copies in 3 different shapes — 2026-08-01
- [#2063](https://github.com/zealchaiwut/commander/issues/2063) Health-strip 'See more →' points at the removed metrics tab — 2026-08-01

## Sprint 1008

Sprint-board reliability and legibility polish — four merged tickets hardening sprint-label validation and three board/history rendering gaps. **Fail fast on non-conforming sprint labels (#2058):** the canonical regex `^sprint-\d+(\.\d+)?$` now backs an actionable, shared error message. `board_service.py` exports `SPRINT_LABEL_FORMAT_ERROR` (the message includes the "free-text labels are not visible on the board" consequence) so `sprint_labels.py` (`POST /api/sprints/batch-labels`) and `sprint_run.py` (`POST /api/sprints/run`) reject bad labels with the same wording instead of a bare `invalid sprint_label`; response shapes and status codes are unchanged (still a 400 / errors array). A new pure helper `find_nonconforming_sprint_labels()` plus a best-effort startup check `_warn_nonconforming_sprint_labels()` scan the zero-quota issues mirror at boot and print a `[startup-warn]` line for any pre-existing `sprint-*` label that fails the regex (e.g. `sprint-viz9001`), so operators learn about issues that are silently invisible on the board. **Backlog row label chips (#2060):** `_smgmtBacklogLabelChipsHtml()` renders compact, colour-coded chips (uat/sit/in-progress/needs-rework/blocked/sprint-*) on each backlog row — capped at 3 with a `+N` overflow chip — so shipped or in-flight work no longer looks identical to untriaged work. **Suppress zombie 0-ticket cards (#2061):** `assemble_board()` now flags a post-run sprint card (`needs_rework`/`ready_to_merge`) with `stale_no_tickets: true` when no open tickets remain; the board render propagates the flag (`_staleNoTicketLabels`) and replaces the Re-run / Reconcile / Merge Sprint action row with a quiet "stale — no tickets" notice so a drained sprint can no longer be re-run or merged into oblivion. **History loading, empty, and error states (#2062):** the History ledger now shows a skeleton while the fetch is in flight (instead of a ~2s silent blank), an inbox-off empty state, and an error state with a Retry button; a new `_histIsLoading()` guard keeps the sub-nav action badge from flashing to zero from cleared data mid-fetch. Router files under `apps/dashboard/routers/` were touched, but only the 400 error-message text changed (not documented at that granularity) and the new `stale_no_tickets` card field is not part of any documented response shape, so `docs/features/api.md` and `SCHEMA.md` are unchanged. Two tickets did not ship in this batch and were re-run in Sprint 1008.1 (below): #2059 (regex consolidation, RETRY_EXHAUSTED on lint) and #2063 (health-strip link, GATE_FAIL).

- [#2058](https://github.com/zealchaiwut/commander/issues/2058) Sprint labels that do not match the board regex are silently invisible — fail fast instead — 2026-08-01
- [#2060](https://github.com/zealchaiwut/commander/issues/2060) Backlog rows show no label information — shipped work looks identical to untriaged work — 2026-08-01
- [#2061](https://github.com/zealchaiwut/commander/issues/2061) Zombie 'Ready to Merge' sprint cards with 0 tickets stay on the board forever, fully actionable — 2026-08-01
- [#2062](https://github.com/zealchaiwut/commander/issues/2062) History tab renders a silent blank for ~2s instead of a loading state — 2026-08-01

## Sprint 1007.2

Tooling and documentation hardening — five follow-ups fixing stale agent-definition paths, a dangerous `--help`, an import crash, a missing slash command, and an incomplete script index. No source runtime, API, schema, or endpoint changes. **Fix stale `dashboard/` paths in agent definitions (#2053):** `.claude/agents/coder.md` and `.claude/agents/tester.md` referenced a `dashboard/` directory that no longer exists after the repo restructure; every path was corrected to `apps/dashboard/` (or plain `scripts/` for helper-script calls like `start_feature.py`, `update_ticket.py`, `comment_ticket.py`, `post_test_report.py`), so the coder and tester agents run their scripts from the right locations. **argparse + `--yes` gate for `resync_issues_mirror.py` (#2054):** the resync script had no argument parser, so `--help` fell through to a live 5-repo GitHub resync that could exhaust the rate limit. It now uses `argparse`: without `--yes`/`--force` it prints a safe dry-run summary of the repos it would touch and exits 1; `--yes` is required to perform the resync, and a new `--repo OWNER/REPO` scopes it to a single repository. Output was also tightened to a per-repo one-line progress format plus a final rows-changed/errors summary. **Fix `sprint_planner.py` import crash (#2055):** the script crashed on import — it inserted `apps/dashboard` ahead of `services/sprint_manager` on `sys.path`, so `from sizing import SIZE_TO_MINUTES` resolved against the wrong module and even `--help` failed. The two `sys.path.insert` calls were reordered (sprint_manager first, dashboard second) and the affected imports annotated so lint passes. **Add the `/estimate` slash command (#2056):** CLAUDE.md advertised a `/estimate <issue-url>` command that did not exist. A new `.claude/commands/estimate.md` implements it — parses an issue URL or bare number (auto-detecting the repo for bare numbers), passes through `--save-comment`/`--save-label`/`--force`, invokes `services/sprint_manager/estimate_issue.py`, and reports size/minutes/confidence plus the saved estimate path. **Complete the `scripts/AGENTS.md` index (#2057):** the script index listed ~10 of ~60 scripts, leaving most of the toolkit undiscoverable. It was rewritten to index every `.py`/`.sh` file directly under `scripts/` (excluding `archive/` and `test_fixtures/`), grouped by lifecycle (Ticket, Sprint, Reporting, Project Setup, Maintenance) with per-script arg summaries and mutation markers (`[GH]`/`[Neon]`/`[DB]`). A new drift gate, `tests/test_2057__scripts_agents_md_coverage.py`, fails on any push if a script is missing from the index. No new DB tables, columns, or endpoints, and no router behavior changed, so `docs/features/api.md` and `SCHEMA.md` are unchanged.

- [#2053](https://github.com/zealchaiwut/commander/issues/2053) Coder and tester agent definitions reference a `dashboard/` directory that does not exist — 2026-08-01
- [#2054](https://github.com/zealchaiwut/commander/issues/2054) resync_issues_mirror.py has no argparse — `--help` performs a live 5-repo GitHub resync — 2026-08-01
- [#2055](https://github.com/zealchaiwut/commander/issues/2055) sprint_planner.py crashes on import — cannot even print --help — 2026-08-01
- [#2056](https://github.com/zealchaiwut/commander/issues/2056) CLAUDE.md advertises a /estimate slash command that does not exist — 2026-08-01
- [#2057](https://github.com/zealchaiwut/commander/issues/2057) scripts/AGENTS.md indexes ~10 of ~60 scripts — most of the toolkit is undiscoverable — 2026-08-01

## Sprint 1006.1

Two test-suite hygiene follow-ups from code review — no source, API, schema, or endpoint changes. **Remove unused `import db as _db` (#1930):** the unused `import db as _db` inside `test_not_blocked_after_clear` in `tests/test_merge_conflict_auto_resolve__1898.py` (leftover from another test's fixture setup) was deleted, along with other now-unused imports; a `DB_PATH` value in the same file was also corrected from `str` to `Path`. **Strengthen weak zero-gh assertion (#1832):** sprint review of #1788 flagged `test_board_service_source_documents_zero_gh` in `test_call_budgets.py` as a substring-presence check (`"zero" in src and "gh" in src`) that proves nothing behavioral. Because the coder-no-test-edits gate forbids editing that graded file, a new `tests/test_issue_1832__remove_weak_zero_gh_test.py` provides independent behavioral coverage instead — it drives the board path via `TestClient` and uses `call_budget_fixture.assert_zero_gh()` to confirm the zero-gh contract holds at runtime for both the cache-miss and cache-hit paths. No new DB tables, columns, or endpoints, and no router behavior changed, so `docs/features/api.md` and `SCHEMA.md` are unchanged.

- [#1930](https://github.com/zealchaiwut/commander/issues/1930) [follow-up] remove unused `import db as _db` in test_merge_conflict_auto_resolve__1898.py — 2026-08-01
- [#1832](https://github.com/zealchaiwut/commander/issues/1832) [follow-up] Strengthen weak zero-gh doc assertion in call-budget test — 2026-08-01

## Sprint 1006

Code-review follow-up sprint — four cleanup tickets hardening error visibility, a git-staging gate, and test-suite hygiene, with no API, schema, or endpoint changes. **Log swallowed exceptions in `_enrich_home_artifact` (#1798):** the two `except Exception` handlers in `_enrich_home_artifact` (`apps/dashboard/routers/brief.py`) previously fell back silently — one when `load_projects()` fails (→ `[]`), one when `get_or_create_project_summary()` fails per project (→ empty `briefSummary`). Each now emits `_log.warning(..., exc_info=True)` before falling back, so real failures surface in the dashboard log while the graceful-degradation behavior is unchanged; the handler still returns the same fallback shape, so no route, parameter, or response changes. **Inline `datetime` import in `_sprint_set_conflict_blocked` (#1908):** the inline `__import__("datetime").datetime.now(__import__("datetime").timezone.utc)` in `_sprint_set_conflict_blocked` (`apps/dashboard/startup.py`) was replaced with a direct `datetime.now(timezone.utc)` using the module-level import; behavior is identical. **Harden lint auto-fix staging; remove stray test files (#1966):** `_lint_autofix_commit` in `services/sprint_manager/gates.py` no longer runs `git add -A` (which could sweep in unrelated untracked files); it now stages only tracked files the fixers actually modified — `git diff --name-only` lists working-tree-vs-index changes for tracked files only, and the commit is skipped entirely when that list is empty. Stray unrelated test files swept into an earlier diff (`test_1944__coder_no_test_edits_uat.py`, `test_remove_three_dead_api_endpoints__1772.py`, `test_trigger_owner_overnight_mode__1946.py`) were removed. **Remove permanently-skipped meta-test (#1925):** an unconditional `pytest.skip()` meta-test in `test_bulk_move_new_sprint_clear_selection__1760.py` was dead noise that never asserted anything. Rather than edit that graded test file (forbidden by the coder-no-test-edits gate), `conftest.py`'s `pytest_collection_modifyitems` now permanently deselects it by nodeid via a new `_PERMANENTLY_DESELECTED_NODEIDS` set, and the module was added to `_LIVE_SERVER_TEST_MODULES`. No new DB tables, columns, or endpoints, and the touched `apps/dashboard/routers/brief.py` change is internal logging only with no route, parameter, or response-shape change, so `docs/features/api.md` and `SCHEMA.md` are unchanged.

- [#1798](https://github.com/zealchaiwut/commander/issues/1798) [follow-up] Log swallowed exceptions in _enrich_home_artifact — 2026-07-22
- [#1908](https://github.com/zealchaiwut/commander/issues/1908) [follow-up] inline __import__("datetime") in _sprint_set_conflict_blocked — 2026-07-22
- [#1925](https://github.com/zealchaiwut/commander/issues/1925) [follow-up] Remove permanently-skipped meta-test in test_bulk_move_new_sprint_clear_selection__1760.py — 2026-07-22
- [#1966](https://github.com/zealchaiwut/commander/issues/1966) [follow-up] Remove stray unrelated test files swept into #1959 diff — 2026-07-22

## Sprint 1004.1

Database-reliability hardening plus three code-review follow-ups. **commander.db corruption hardening (#1901):** after the live DB was found truncated (~4 KB from ~30 MB) during an unattended run, `init_db()` now runs `PRAGMA integrity_check` at startup (`apps/dashboard/db.py`) and aborts with a CRITICAL log naming the most recent local backup instead of 500-looping on a corrupt file; new `check_db_integrity()` and `run_wal_checkpoint()` helpers back this. Hourly rolling local backups (5 generations deep) are written via SQLite's online-backup API to `.commander/db-backups/` by a self-rescheduling timer started at server boot (`apps/dashboard/backup.py` + a parallel implementation in `services/sprint_manager/backup.py`); the scheduler registers the backup dir with `db.py` so integrity failures surface a copy-paste restore hint. Root-cause analysis and a step-by-step recovery procedure are documented in `docs/runbook-db-recovery.md`. **Guard fire-and-forget board broadcast (#1826):** `invalidate_board()` (`routers/board_cache.py`) now wraps the `board_invalidated` SSE broadcast in `_guarded_broadcast()`, which awaits the coroutine and logs any exception as a `log.warning` rather than letting it surface as an unhandled-task warning at GC time — for both the `create_task` (async) and `run_coroutine_threadsafe` (threadpool) paths. **Remove committed SQLite sidecar files (#1965):** the tracked `dashboard.db-shm`/`dashboard.db-wal` WAL/shm sidecars were removed and `*.db-wal`/`*.db-shm`/`*.db-journal` added to both `.gitignore` and `apps/dashboard/.gitignore` so they are never committed again. **Visibility-guard remaining pollers (#1796):** the SSE fallback poll (`_smgmtSseStartFallback`), the 10-min timeline re-fetch (`_smgmtLivePollRestart`), and the bulk-post poll (`_bcStartPostPoll`) in `apps/dashboard/static/project.html` now route through `visibilityInterval` (falling back to `setInterval` when unavailable) so they pause while the tab is hidden. No new DB tables, columns, or endpoints, so `SCHEMA.md` and `docs/features/api.md` are unchanged.

- [#1901](https://github.com/zealchaiwut/commander/issues/1901) [Reliability] commander.db corrupted during unattended run — root cause + WAL/backup hardening — 2026-07-20
- [#1826](https://github.com/zealchaiwut/commander/issues/1826) [follow-up] Guard fire-and-forget board_invalidated broadcast task against unhandled exceptions — 2026-07-20
- [#1965](https://github.com/zealchaiwut/commander/issues/1965) [follow-up] Remove committed SQLite WAL/shm sidecar files and gitignore them — 2026-07-20
- [#1796](https://github.com/zealchaiwut/commander/issues/1796) [follow-up] Visibility-guard remaining network pollers (SSE fallback, timeline refetch, bulk-post) — 2026-07-20
## Sprint 1004

Two code-review follow-ups clearing dead frontend code and silent backend error-swallowing. **Remove dead Logs-tab run functions (#1870):** after the Logs-tab Timeline/run-expansion UI was removed, five now-unreachable functions in `apps/dashboard/static/project.html` — `logsToggleRun`, `_logsTicketStatsHtml`, `_logsFetchTicketStats`, `_logsIcaCostHtml`, `_logsFetchIcaCost`, and the live-stream helper `_logsConnectLive` — were deleted along with their orphaned `_logsState` fields (`expanded`, `ticketStats`/`loadingStats`, `icaCost`/`loadingIcaCost`). No behavior change; HTML-only, so it goes live on a hard refresh. **Log broad `except Exception` in inline aggregate builders (#1753):** the three inline board builders in `apps/dashboard/routers/board_service.py` (`_build_outcome_inline`, `_build_finish_card_inline`, `_build_branch_status_inline`) previously swallowed all exceptions silently (`except Exception: pass`/`return`), hiding real errors behind a blank card. Each now logs `log.warning(..., exc_info=True)` before returning its fallback, so failures surface in the dashboard log while the graceful-degradation behavior is preserved. No DB tables, columns, or endpoints changed, so `SCHEMA.md` is unchanged.

- [#1753](https://github.com/zealchaiwut/commander/issues/1753) [follow-up] #1744 broad except Exception swallows errors in inline aggregate builders — 2026-07-20
- [#1870](https://github.com/zealchaiwut/commander/issues/1870) [follow-up] Remove dead logsToggleRun/_logsTicketStatsHtml/_logsIcaCostHtml after Timeline removal — 2026-07-20

## Sprint 1005

Follow-up sprint closing two code-review gaps — one API-doc accuracy fix, one test-coverage upgrade — with no source-code changes. **api.md `force` query parameter (#1971):** the `GET /api/dev-report` section of `docs/features/api.md` was missing the `force` query parameter, so the Hermes commander-api skill (which consumes this doc live) had no way to know the endpoint can regenerate its artifact on demand. Added the row documenting that `force=true` (or `1`) regenerates the dev-report artifact inline, persists it to the brief-artifacts store, then returns it, whereas without `force` the endpoint returns the stored artifact or `404` if none exists for the requested date. Docs-only. **Fetch-spy harness wired into a real page-load test (#1831):** the frontend call-budget guarantees were previously enforced only by source-regex checks — exactly the class of miss that let the #982 SSE regression ship green (per CLAUDE.md #1746). A new behavioral suite, `tests/frontend/call-budget-page-load.test.mjs`, drives the real loaders (`loadSprintMgmt`, `_histLoadLedger`, `_smgmtRunningFirstPaint`) through the shared `installFetchSpy`/`assertFetchBudget` harness (`call-budget-helpers.mjs`) and asserts the actual per-load fetch budgets (Board = 1 `/api/board`, History = 1 `/api/sprints/history`, Running first paint = 1 `/api/running`), including a negative case where a double board-load trips `assertFetchBudget`. A companion `tests/test_fetch_spy_harness__1831.py` confirms the harness is wired in and the budgets are behaviorally enforced. Test-only. No new DB tables, columns, or endpoints, so `SCHEMA.md` is unchanged.

- [#1831](https://github.com/zealchaiwut/commander/issues/1831) [follow-up] Wire fetch-spy harness into a real page-load test; frontend budgets are source-regex only — 2026-07-20
- [#1971](https://github.com/zealchaiwut/commander/issues/1971) [follow-up] api.md dev-report section omits the force query parameter (AC gap) — 2026-07-20

## Sprint 1003.2

Follow-up/bug-fix sprint hardening the autonomous sprint runner, estimator, and dashboard internals — eight tickets, all behavioral fixes with no API, schema, or endpoint changes. **CODER_NO_WORK false-fail fix (#1935):** `_find_feature_branch` in `sprint_manager.py` now queries `origin` live via a new `_ls_remote_feature_branch(issue_num)` helper (`git ls-remote origin refs/heads/feature/<N>-*`) as the primary authoritative check, so a branch the coder just pushed is found even when the local remote-tracking refs are stale — the previous `git branch -r` scan is kept only as a fallback for transient `ls-remote` failures. The CODER_NO_WORK detection in `run_sprint_loop` calls `ls-remote` before declaring no work: a real `ls-remote` failure now classifies as `ENV_ERROR` (not CODER_NO_WORK) so the ticket is not wrongly retried, and multi-branch matches reuse the same issue-commit disambiguation as `_find_feature_branch`. **Estimator CLI mirror sync_ts (#1937, subsumes #1916):** `estimate_issue.main()` no longer stamps a wall-clock `now` as `sync_ts` (which made the stale-hit guard in `fetch_issue` a no-op); a new `_get_mirror_sync_ts(repo)` reads the mirror's actual last-sync timestamp from `db.get_sync_updated_at("issues:{repo}")` — same contract as `dispatch._get_mirror_sync_ts` — so the guard triggers a live fetch only when the issue was modified after the mirror ran. The inverted AC3/AC4 tests merged red under #1916 are corrected. **Token ceiling enforced in pipeline mode (#1948):** `_apply_token_ceiling` moved from `sprint_manager.py` to `state.py` (breaking a circular import) and `_run_pipeline_dispatch` in `pipeline.py` now accepts a `token_ceiling` argument and checks cumulative spend after each dispatch level — the running level always finishes, but once spent ≥ ceiling no further levels dispatch and `SprintState.ceiling_hit` is set. `run_sprint` threads the effective ceiling (`--token-ceiling` or `sprint.yaml`) into the pipeline path. **coder-no-test-edits gate catches deletes/renames (#1949):** the gate in `gates.py` switched its diff filter from `--diff-filter=ACM` to `--diff-filter=CMD` (Copied/Modified/Deleted) plus a separate `--diff-filter=R` pass, so TDD-written *new* test files (Added) pass through while edits, deletions, copies, and renames of existing grading tests are blocked. **commander_report atomic write uses a unique temp (#1951):** `write_commander_report` in `sprint_webhook_service.py` now creates its temp file with `tempfile.mkstemp(dir=path.parent, suffix=".tmp")` instead of a shared `path.with_suffix(".tmp")`, so concurrent writers can no longer clobber each other's temp before the atomic `rename`. **estimate-job store cap hardened (#1906):** `_evict_if_needed` in `routers/estimate_jobs.py` now evicts terminal (done/failed) jobs first and, if still at/above `_MAX_JOBS`, sheds the oldest queued/running entries too — preventing unbounded growth when a hung subprocess parks every job in a non-terminal state; the cap is also enforced on the lazy-load path in `get_estimate_job`. **Duplicate snapshot on SSE connect removed (#1819):** `get_sprint_live_stream` in `routers/sprint_live.py` reuses the initial `snapshot` it already computed to seed per-issue log offsets, instead of calling `get_sprint_live_snapshot` a second time on every connect. **home.html `_projBySlug` populated (#1978):** the Dev Report home view now builds `_projBySlug` from the report metadata so project badges color correctly and Run-sprint sends the full `owner/repo`; the Hermes exporter (`scripts/export_hermes_report.py`) adds `project` (slug), `repo`, `icon`, and `color` to each project entry to supply that metadata. No new DB tables, columns, or endpoints, and the touched `apps/dashboard/routers/` changes are all internal (eviction logic, snapshot dedup, atomic-write temp path) with no route, parameter, or response-shape change, so `docs/features/api.md` and `SCHEMA.md` are unchanged.

- [#1819](https://github.com/zealchaiwut/commander/issues/1819) [follow-up] Duplicate live-snapshot computation on SSE connect — 2026-07-21
- [#1906](https://github.com/zealchaiwut/commander/issues/1906) [follow-up] estimate-job store cap can miss when all jobs are running/queued — 2026-07-21
- [#1935](https://github.com/zealchaiwut/commander/issues/1935) [bug] CODER_NO_WORK false-fails: _find_feature_branch checks remote-tracking refs without fetching — 2026-07-21
- [#1937](https://github.com/zealchaiwut/commander/issues/1937) [follow-up] #1916 estimator CLI still passes wall-clock sync_ts; its AC tests merged red/inverted — 2026-07-21
- [#1948](https://github.com/zealchaiwut/commander/issues/1948) [follow-up] Enforce token ceiling in pipeline-mode dispatch — 2026-07-21
- [#1949](https://github.com/zealchaiwut/commander/issues/1949) [follow-up] coder-no-test-edits gate misses deleted/renamed test files — 2026-07-21
- [#1951](https://github.com/zealchaiwut/commander/issues/1951) [follow-up] commander_report atomic write uses a shared temp path — 2026-07-21
- [#1978](https://github.com/zealchaiwut/commander/issues/1978) [follow-up] Populate _projBySlug in home.html so badges color and run-sprint sends full repo — 2026-07-21

## Sprint 1002.3

Replaces the home page brief UI with a per-project Dev Report view. **Home page Dev Report view (#1961):** the self-contained `apps/dashboard/static/home.html` now fetches `GET /api/dev-report` and renders one collapsible block per project instead of the old brief layer (KPI strip, decisions planner, AI one-liner cells). Each block has a header row (project badge/name, a `.pbstatus` run/idle/blocked pill, counts summary) and body bullets that render only for non-empty buckets — **shipped** (label, goal, done count, PR#), **fixed** (issue#, title), **stale** (kind, ref, age_days), and **waiting** (label, ticket count, est hours); idle projects with all buckets empty collapse to the header line only. Long bucket lists reuse the existing `_briefCollapsibleRows` first-3-plus-"see more" pattern. A footer renders `Cost today: {cost} · window {window_start}->{window_end}`, and each block keeps the Open-project CTA, a Review link into the project's UAT view when `waiting` is non-empty, and a Run-sprint button (via the existing `runSprintAction`) when a project is idle with a ready backlog sprint. The `?date=` picker and Add Project entry point are preserved, a 404 from `/api/dev-report` shows a graceful "No report yet — runs nightly at 05:45" empty state, and all old `.pbrief` grid CSS and brief render functions (`loadBrief`, `renderKpis`, `renderDecisions`, planner/AI cells) are deleted. Styling uses only `tokens.css` variables; the change is HTML-only (no esbuild/bundle step) so it goes live on a hard refresh. The consumed `GET /api/dev-report` endpoint lives in `apps/dashboard/routers/dev_report.py` and is documented in `docs/features/api.md`. No new DB tables, columns, or endpoints beyond the documented route, so `SCHEMA.md` is unchanged.

- [#1961](https://github.com/zealchaiwut/commander/issues/1961) Replace home page brief UI with Dev Report view — 2026-07-17

## Sprint 1002.2

Process-hardening sprint for the documenter agent. **API doc update duty (#1962):** the documenter prompt in `services/sprint_manager/post_sprint.py` now lists `docs/features/api.md` among the docs to review and adds a mandatory "Router Changes → API Doc Update" step — whenever a sprint diff touches any file under `apps/dashboard/routers/`, the documenter must review and update `docs/features/api.md` (new routes, changed signatures/parameters, changed response shapes or status codes). This keeps the Hermes commander-api skill, which consumes `docs/features/api.md` live, from serving stale endpoint docs. Prompt/process change only — no DB tables, columns, or endpoints, so `SCHEMA.md` is unchanged.

- [#1962](https://github.com/zealchaiwut/commander/issues/1962) Add API doc update duty to documenter prompt — 2026-07-17

## Sprint 1002

Adds the nightly Hermes dev-report contract exporter. **Nightly Hermes dev-report exporter (#1959):** a new standalone CLI, `scripts/export_hermes_report.py`, queries the Commander SQLite DB and writes a versioned JSON contract that Hermes consumes. It degrades gracefully — any failure logs to stderr, exits `0`, and leaves any existing output file untouched. Output path resolves in order `--output` → `$COMMANDER_REPORT_PATH` → `$HERMES_HOME/contracts/commander_report.latest.json` → `~/.hermes/contracts/commander_report.latest.json`; the DB path resolves `--db-path` → `$DB_PATH`. The contract carries `for_date` (Asia/Bangkok date), `generated_at`, `window_start`/`window_end`, a per-project `projects[]` list (each with `name`, `status` — `shipped`/`in_progress`/`blocked`/`waiting_signoff`/`idle` — plus `in_progress`, `shipped`, `fixed`, `stale`, `waiting`, and `counts`), `cost` + `cost_source` (price-map $ figure from `settings_kv.app_config`, else raw token count, else `unknown`), and the legacy rollup keys `completed`/`needs_review`/`dead_letter`. Stale detection spans three kinds with configurable thresholds — blocked/rework issues (`--stale-blocked-days`, default 3), `ready_to_merge` sprints awaiting sign-off (`--stale-waiting-days`, default 2), and planning-state sprints (`--stale-backlog-days`, default 7). Fixed-issue detection diffs the current blocked set against the previous run's, persisted atomically to `.commander_export_state.json` beside the output file. All writes are atomic (temp file + `os.replace`); `--dry-run` prints the contract to stdout without touching any file. On a real run the payload is also upserted into the existing `brief_artifacts` table (issue #841) under scope `dev_report`, keyed by Bangkok date. No new DB tables, columns, or endpoints, so `SCHEMA.md` is unchanged.

- [#1959](https://github.com/zealchaiwut/commander/issues/1959) Add nightly Hermes dev-report contract exporter — 2026-07-17

## Sprint 120

Feature sprint hardening the autonomous sprint runner for unattended (Hermes-driven) operation — five tickets adding a dead-letter registry, a token ceiling, a coder test-edit gate, a file-based outcome report, and trigger-owner/overnight run metadata. **Dead-letter registry (#1942):** `IssueState` (in `state.json`) gains `fix_attempts: int` (default `0`) and `last_error: str | None`; the fix loop in `sprint_manager.py` increments `fix_attempts` and records `last_error` after each failed round and persists `state.json` immediately. When the fix loop is exhausted, an entry is appended to a new `dead_letter` list on `SprintState` (`{ticket_id, title, attempts, last_error}`), de-duplicated by `ticket_id` so a rerun never re-adds the same ticket; the list round-trips through `state.json`, and `generate_sprint_summary` (`summary.py`) renders a **Dead Letter** table. **Token ceiling (#1943):** a new `--token-ceiling <N>` CLI flag (and `token_ceiling` key in `sprint.yaml`, `config.py`) sets a hard cumulative-token limit; `0`/absent disables it (no behavior change). After each ticket, `_apply_token_ceiling` compares `total_tokens_in + total_tokens_out` against the ceiling — the current ticket always finishes, but once spent ≥ ceiling no further tickets dispatch, `SprintState.ceiling_hit` is set `true`, and a `[token-ceiling] Token ceiling reached (X tokens used, limit N)` line is logged once. **Coder-no-test-edits gate (#1944):** a new gate in `gates.py` (`_gate_coder_no_test_edits`) runs first in `_run_quality_gates` (cheapest — a pure `git diff --name-only` scan) and fails any coder diff touching a blocked path (default `tests/**`). Blocked globs are configurable via `CODER_BLOCKED_PATH_PATTERNS`, exemptions via `CODER_TEST_PATH_ALLOWLIST`, and the whole gate via `COMMANDER_GATE_CODER_NO_TEST_EDITS`/`--gate-coder-no-test-edits` (default on); a failure reverts the ticket to SIT naming the offending files. **commander_report.latest.json (#1945):** at every sprint terminal state (finish, kill, error) a rich outcome snapshot is written **atomically** (write-to-temp + rename) to `COMMANDER_REPORT_PATH` (default `/var/run/commander/commander_report.latest.json`) so external pollers (Hermes) always have a consistent artifact regardless of webhook delivery. A single builder (`build_commander_report`) produces both the file and the webhook payload — no field drift — with top-level `run_id`, `trigger`, `branch`, `summary`, `completed`, `needs_review`, `dead_letter`, `cost`, and `actions`. A missing parent directory logs a clear error and leaves the run (and any previous file) untouched. **Trigger-owner + overnight mode (#1946):** `POST /api/sprints/run` (`SprintMgmtRunBody`) now accepts optional `by` (stored as `triggered_by` in `plan.json` and echoed in the report), `mode` (only `overnight` accepted; any other value → **HTTP 400**), and `target_branch`. In `overnight` mode the run defaults `--target-branch` to `develop` when no explicit target is given and runs the full test suite against the target branch after **each** ticket merge (`_run_per_ticket_tests_overnight`, best-effort — a failure never stops the run). `COMMANDER_REPORT_PATH` is documented in `.env.example`; the changed webhook payload and new request fields are documented in `SCHEMA.md`.

- [#1942](https://github.com/zealchaiwut/commander/issues/1942) Persist dead-letter registry for exhausted sprint tickets — 2026-07-14
- [#1943](https://github.com/zealchaiwut/commander/issues/1943) Add configurable token/cost ceiling to sprint runs — 2026-07-14
- [#1944](https://github.com/zealchaiwut/commander/issues/1944) Add sprint gate blocking coder from editing test files — 2026-07-14
- [#1945](https://github.com/zealchaiwut/commander/issues/1945) Write commander_report.latest.json at sprint end for Hermes — 2026-07-14
- [#1946](https://github.com/zealchaiwut/commander/issues/1946) Add trigger-owner metadata and overnight run mode to sprint runs — 2026-07-14

## Sprint 119.2

Follow-up sprint clearing eight code-review/regression follow-ups batched across sprint-119, 119.1, and 119.2 — two behavioral dispatch/metrics fixes, two frontend regressions, and repo/test hygiene. **Dispatch stale-hit guard uses mirror sync timestamp (#1915):** the stale-hit guard in `_mirror_issue_body` (`dispatch.py`) was comparing the issue's GitHub `updatedAt` against wall-clock *now* (stamped at start-of-dispatch), so nearly every dispatch treated its mirror hit as stale and fell back to a live `gh` fetch. A new `db.get_sync_updated_at(key)` returns `sync_state.updated_at` (when the mirror last performed a successful ETag sync); dispatch's new `_get_mirror_sync_ts(repo)` reads `issues:{repo}` and passes it as `sync_ts`. The guard condition is corrected accordingly: a mirror record is **fresh** when `updatedAt <= sync_ts` (the issue was last modified at or before the last sync, so the sync captured current state) and **stale** only when modified after — reversing the previous `>=` that forced the fallback. The same corrected condition is applied in `estimate_issue.fetch_issue`. **fetch_issue stale-hit guard wired up (#1916):** the guard added in #1813 was inert because the sole caller, `estimate_issue.main()`, never passed `sync_ts`; it now stamps a UTC timestamp and threads it into `fetch_issue`. **by_sprint metrics numeric sort (#1869):** `_compute_analytics_metrics` in `startup.py` now sorts `sprint-*-state.json` files by numeric sprint number (`_sprint_num_key`) instead of lexically, so `sprint-9` sorts before `sprint-10` (oldest→newest). **SSE fallback polls restored (#1917):** `_smgmtLanesUpdate` and `_smgmtInspectorStreamRestart` in `project.html` guarded the REST fallback with `if (!_smgmtSseEs)`, but `_smgmtSseEs` is a `Map` (always truthy), so the fallback polls were permanently dead when SSE wasn't connected for a given label; both now check `!_smgmtSseEs.has(label)` / `.has(_smgmtInspectorLabel)`. **Bulk move-to-new-sprint clears selection (#1760):** `_smgmtMoveModalPickBulk` now calls `_smgmtClearSelection()` after a successful move to a *new* sprint, so moved tickets no longer linger visually selected in the backlog. **Repo/test hygiene:** removed the committed machine-specific `venv` symlink from the repo root and hardened `.gitignore` (`venv/` → `venv`) so the symlink form is ignored (#1825); consolidated duplicate/mis-named test files for #1749 and #1898 into their canonical `test_<feature>__<issue>.py` names (#1907); removed stale `DELETE /api/alerts/{idx}` callers left by #1772 from the test suite (#1793). No new tables, columns, or endpoints (`get_sync_updated_at` is a new accessor over the existing `sync_state.updated_at`), so `SCHEMA.md` is unchanged.

- [#1760](https://github.com/zealchaiwut/commander/issues/1760) [follow-up] Bulk move-to-new-sprint never clears selection Set; moved tickets linger in backlog — 2026-07-14
- [#1793](https://github.com/zealchaiwut/commander/issues/1793) [follow-up] Remove stale DELETE /api/alerts/{idx} callers left by #1772 — 2026-07-14
- [#1825](https://github.com/zealchaiwut/commander/issues/1825) [follow-up] Remove committed machine-specific `venv` symlink from repo root — 2026-07-14
- [#1869](https://github.com/zealchaiwut/commander/issues/1869) [follow-up] Sort by_sprint metrics numerically (oldest→newest) not lexically — 2026-07-14
- [#1907](https://github.com/zealchaiwut/commander/issues/1907) [follow-up] duplicate + mis-named test files for #1749 and #1898 — 2026-07-14
- [#1915](https://github.com/zealchaiwut/commander/issues/1915) [follow-up] Dispatch stale-hit guard passes wall-clock now, forcing a live gh fetch nearly every dispatch — 2026-07-14
- [#1916](https://github.com/zealchaiwut/commander/issues/1916) [follow-up] fetch_issue stale-hit guard is inert — sole caller does not pass sync_ts — 2026-07-14
- [#1917](https://github.com/zealchaiwut/commander/issues/1917) [follow-up] SSE log-fallback checks treat _smgmtSseEs Map as nullable — fallback polls now permanently dead — 2026-07-14

## Sprint 118.1

Two more code-review follow-ups closing gaps left by earlier sprint-118 tickets. **Aggregate branch_status exists inference (#1752):** the zero-gh aggregate finish-card path (`_build_branch_status_inline` in `routers/board_service.py`) previously always returned `exists=False` because it makes no subprocess/`gh` call to probe the branch — so a running sprint's branch state diverged between the aggregate flag ON and OFF. It now infers `exists=True` from two zero-quota sources: `lifecycle_state == "running"` (the branch must be alive) or a non-null `pr_number` on the DB row (the branch was alive when the PR opened); `exists=False` only when neither confirms it. `_build_sprint_card` threads `lifecycle_state` through to the helper. **docs_service non-UTF-8 handling (#1879):** `docs_service.get_doc` wraps `read_text(encoding="utf-8")` in a try/except so a doc file with non-UTF-8 bytes returns **HTTP 422** (`File contains non-UTF-8 bytes and cannot be decoded: <reason>`) instead of leaking a `UnicodeDecodeError` as a 500. No new tables; the docs 422 behavior is documented in `SCHEMA.md`.

- [#1752](https://github.com/zealchaiwut/commander/issues/1752) [follow-up] #1744 aggregate branch_status always exists=False — finish-card branch state diverges flag ON vs OFF — 2026-07-14
- [#1879](https://github.com/zealchaiwut/commander/issues/1879) [follow-up] docs_service.get_doc read_text has no handling for non-UTF-8 files — 2026-07-14

## Sprint 118

Backend reliability follow-up sprint — six code-review follow-ups hardening the mirror-read path, the batch-todos endpoint, the rerun flow, and multi-label SSE. **Batch todos slug cap/dedup (#1797):** `GET /api/todos?projects=` now collapses duplicate slugs (`dict.fromkeys`) and returns **HTTP 400** when more than `MAX_BATCH_SLUGS=50` unique slugs are requested, so a malformed or oversized query can't fan out unbounded `list_todos` calls. **Metrics needs-rework mirror parity (#1803):** `_bulk_rework_from_mirror` in `routers/metrics.py` now reads the mirror with `state="open"` so the mirror-backed needs-rework count matches the `gh` fallback (`_count_rework_tickets`), which was already open-only — closed needs-rework tickets no longer inflate the mirror path's count. **Stale-hit sync_ts guard wired into body fetches (#1813):** `_fetch_dispatch_issue_body` and `estimate_issue.fetch_issue` now accept a `sync_ts` and pass it through so mirror records whose `updated_at` predates the dispatch/estimate timestamp trigger a live fetch instead of serving a stale body; `_dispatch_coder` stamps `sync_ts` at start-of-dispatch, and `sprint_manager._build_design_block` now routes its own fetch through the shared `_mirror_issue_body` helper (one mirror → live-fallback path with consistent logging). **Mirror label invalidation on transition (#1814):** new `db.invalidate_mirrored_issue(repo, issue_number)` deletes one issue's mirror row (best-effort, repopulated by the next ~60s ETag sync); `state_machine.transition` calls it after every label write so `_get_issue_labels` can't return a label set that predates the write. **Rerun manifest write error handling (#1840):** the rerun manifest write in `routers/sprint_run.py` is now wrapped in try/except — on `OSError` it rolls the sub-label sprint back to `needs_rework` (`end_reason="manifest_write_failed"`) in both `plan.json` and the DB and raises **HTTP 500** naming the stranded relabeled tickets, instead of silently leaving tickets moved but never dispatched (the #1827 failure mode via I/O error). **SSE all running labels (#1818):** the sprint-management SSE client (`project.html`) now opens a stream for every running sprint label, not just the first, so a board with multiple concurrent running sprints gets live updates for all of them. No new tables; the batch-todos 400 behavior is documented in `SCHEMA.md`.

- [#1797](https://github.com/zealchaiwut/commander/issues/1797) [follow-up] Cap/dedup slug list in batch todos endpoint — 2026-07-14
- [#1803](https://github.com/zealchaiwut/commander/issues/1803) [follow-up] metrics needs-rework count diverges between mirror (all-state) and gh fallback (open-only) — 2026-07-14
- [#1813](https://github.com/zealchaiwut/commander/issues/1813) [follow-up] Wire stale-hit sync_ts guard into mirror body call sites (#1782) — 2026-07-14
- [#1814](https://github.com/zealchaiwut/commander/issues/1814) [follow-up] Mirror-first label reads may serve stale labels in transition guards (#1783) — 2026-07-14
- [#1818](https://github.com/zealchaiwut/commander/issues/1818) [follow-up] SSE board connects only the first running sprint label — 2026-07-14
- [#1840](https://github.com/zealchaiwut/commander/issues/1840) [follow-up] Rerun manifest write has no error handling and runs after labels are already moved — 2026-07-14

## Sprint 117.1

Backend reliability follow-on — clears three confirmed metrics/SSE/memory bugs and fixes a post-sprint orphan-sweep false-fail. **3 backend/reliability bugs (#1897):** (1) *cross-project metrics leak* — `_fetch_sprint_agent_run_rows` in `live_metrics.py` now takes a `project` arg and scopes `agent_runs` rows to that project (falling back to legacy blank/NULL-project rows only when no project-scoped rows exist), so one project's running-sprint metrics no longer bleed into another's; `running_service.py` passes the project through. (2) *dead SSE invalidation broadcast* — `invalidate_board` in `routers/board_cache.py` could not broadcast `board_invalidated` from a threadpool (sync `def`) route because `asyncio.get_running_loop()` raises there; it now falls back to `run_coroutine_threadsafe` on the main loop captured at startup via a new `set_main_loop` (wired in `server.py`'s lifespan). (3) *unbounded estimate-job store* — `routers/estimate_jobs.py` now caps the in-memory `_estimate_jobs` dict at `_MAX_JOBS=200` (evicting oldest done/failed jobs, never running/queued) and prunes persisted job files older than 24h on each new job. **Orphan sweep false-fail (#1887):** the reconciler's `_manager_pid_file` / `_is_manager_pid_alive` in `routers/sprint_reconcile_service.py` now resolve the manager PID file under the sprint's **project** root (e.g. `~/dev/perf-coach/.commander/sprints/<label>-pid`) instead of the dashboard repo root, and `_github_reconcile_row` checks the live manager PID before settling a running row — so a post-sprint documenter/reviewer phase no longer false-fails as `needs_rework "orphaned (no live process)"` while the manager is still alive. No new tables or endpoints.

- [#1887](https://github.com/zealchaiwut/commander/issues/1887) Orphan sweep false-fails sprints during post-sprint phase (documenter/reviewer) — 2026-07-13
- [#1897](https://github.com/zealchaiwut/commander/issues/1897) [Review] 3 confirmed backend/reliability bugs on develop: cross-project metrics leak, dead SSE invalidation broadcast, unbounded estimate-job store — 2026-07-13

## Sprint 117

Reliability and autonomous-loop hardening sprint — unblocks the Hermes autonomous sprint-complete flow on merge conflicts, plugs a SQLite fd leak, and closes out three code-review follow-ups. **Merge-conflict auto-resolve + needs-human signal (#1898):** when a sprint branch is synced into `develop`, append-only conflicts in `SCHEMA.md` / any `models.py` now auto-resolve via `git merge-file --union` (alongside the existing `docs/**.md` doc-merge path), gated on an empty diff3 base section so overlapping edits to existing lines are never silently merged (`_is_union_merge_safe_path` / `_has_overlapping_conflict_in_diff3` / `_resolve_union_merge_conflicts` in `startup.py`). Genuine conflicts now surface structurally: `_gh_merge_branch_via_pr` fills a `conflict_detail_out` dict, `POST .../complete-step` persists the block to `plan.json` (`conflict_blocked: {files, at}`) and returns a structured **HTTP 409** `{code:"merge_conflict_needs_human", files, label, message}`, and a new `GET /api/projects/{owner}/{repo_name}/sprints/{label}/conflict-status` lets autonomous callers poll for the block and skip instead of hanging (a later clean merge clears the flag). **SQLite fd leak (#1749):** `db.get_conn()` is now a `@contextlib.contextmanager` that closes the connection on exit — previously every call leaked the fd. **rerun_dispatch_error wired into exit path (#1839):** `run_sprint()`'s early-exit branch now honors `pf.rerun_dispatch_error`, setting the sprint to `needs_rework` (`end_reason="rerun-dispatch-error"`) in both `plan.json` and the DB and exiting non-zero instead of leaving the field dead. **SSE EventSource close (#1817):** the sprint-management SSE client (`project.html`) now calls `es.close()` in its `complete` and `onerror` handlers before falling back to polling, preventing orphaned connections and duplicate log lines. New endpoint documented in `SCHEMA.md`.

- [#1749](https://github.com/zealchaiwut/commander/issues/1749) Fix sqlite fd leak: get_conn() never closes connections — 2026-07-13
- [#1817](https://github.com/zealchaiwut/commander/issues/1817) [follow-up] SSE EventSource not closed on error/complete — orphaned connection + duplicate log lines — 2026-07-13
- [#1839](https://github.com/zealchaiwut/commander/issues/1839) [follow-up] Wire rerun_dispatch_error into the sprint_manager exit path — 2026-07-13
- [#1898](https://github.com/zealchaiwut/commander/issues/1898) [Hermes loop] Autonomous sprint-complete stalls on merge conflict — auto-resolve append-only conflicts + structured 409/conflict-status — 2026-07-13

## Sprint 116

API-surface sprint — bearer-token auth, async estimate jobs, a sprint-finished webhook, and an automated code-state snapshot. **Static bearer-token auth on write endpoints (#1864):** a single optional `COMMANDER_API_TOKEN` (set in `apps/dashboard/.env`) gates all `POST/PUT/PATCH/DELETE` requests behind `Authorization: Bearer <token>` (401 on mismatch); `GET`/`HEAD`/`OPTIONS`/SSE stay open, and `127.0.0.1`/`::1`/`localhost` callers (same-host hooks) are exempt. When the var is unset there is no auth (default, unchanged behavior). Enforcement is a single `@app.middleware("http")` (`_bearer_auth` in `server.py`) delegating to `bearer_auth_gate` in `routers/auth.py`; served HTML pages get a `<script>` injected (`inject_auth_script`) that monkey-patches `window.fetch` to add the header on non-GET requests, so the browser UI keeps working with no frontend changes. **Async estimate jobs + whole-sprint batch estimate (#1863):** `POST /api/issues/{issue_id}/estimate?async=1` now returns `202 {"job_id": ...}` immediately (the sync form is unchanged); `POST /api/sprints/{sprint_label}/estimate?project=` (202) starts a batch job over every issue in a sprint; poll `GET /api/estimate-jobs/{job_id}` for `{status, progress:[...]}`. Jobs run in FastAPI `BackgroundTasks`, persist to `<project>/.commander/estimate-jobs/{id}.json`, and reload from disk on cache miss (survives restarts). Logic lives in `routers/estimate_jobs.py`. **Sprint-finished webhook (#1865):** `POST` sprint run accepts an optional `callback_url` (validated http/https); a background monitor thread waits for the sprint subprocess to exit and best-effort POSTs an outcome document `{project, sprint_label, outcome, duration_sec, tickets:[{number,status}], summary_url?}` where `outcome` is `finished`/`needs_rework`/`killed`. Killing a sprint fires the webhook with `outcome:"killed"`. When `COMMANDER_API_TOKEN` is set, supplying a `callback_url` itself requires the bearer token (403 otherwise). Logic lives in `routers/sprint_webhook_service.py`. **Code-state snapshot (#1862):** at sprint finish `sprint_manager.py` runs `generate_code_state_snapshot` (`services/sprint_manager/code_state.py`, backed by `scripts/generate_code_state.py`) to regenerate `docs/architecture/code-state.md` and commit/push it to the sprint branch — non-fatal (failures print a WARNING and never abort the pipeline). `.env.example` documents `COMMANDER_API_TOKEN`; new endpoints and the callback field are documented in `SCHEMA.md`.

- [#1862](https://github.com/zealchaiwut/commander/issues/1862) Code-state snapshot: generate docs/architecture/code-state.md at sprint finish — 2026-07-13
- [#1863](https://github.com/zealchaiwut/commander/issues/1863) API: async estimate jobs + whole-sprint batch estimate endpoint — 2026-07-13
- [#1864](https://github.com/zealchaiwut/commander/issues/1864) API: static bearer-token auth on write endpoints — 2026-07-13
- [#1865](https://github.com/zealchaiwut/commander/issues/1865) API: sprint-finished webhook — optional callback_url on sprint run — 2026-07-13

## Sprint 115

Read-only docs API pass — two new GET endpoints expose project markdown and the agent operate guide over HTTP, no DB or GitHub calls involved. **Read-only docs endpoints (#1859):** `GET /api/projects/{slug}/docs` lists a project's allowed `.md` files (`docs/**` plus the root allowlist `README.md`/`CHANGELOG.md`) as `[{path, size, mtime}]`, and `GET /api/projects/{slug}/docs/{path}` returns one file as `{path, content}`. The clone root is resolved from the slug via `docs_service.resolve_clone_root` (honours nested `~/dev/<slug>/main/` and flat `~/dev/<slug>/` layouts; `COMMANDER_PROJECTS_BASE` overrides the base). `get_doc` layers path-security checks in order — reject absolute paths, non-`.md` extensions, and `..` segments (400); verify the resolved path stays inside the clone root and is in the allowed set (400); reject symlinks (400); 404 if missing — so the endpoint can only ever serve docs, never source or secrets. Logic lives in `routers/docs.py` + `routers/docs_service.py`. **Agent operate guide (#1861):** `GET /api/agent-guide` returns `{content, version}` for the new `docs/agent-guide.md` (5 canonical workflow recipes), where `version` is a 16-hex-char SHA-256 fingerprint of the content (stable across unchanged reads); 404s with a helpful message if the file is absent. `GUIDE_PATH` is exposed at module level for test monkeypatching. Logic lives in `routers/agent_guide.py` + `routers/agent_guide_service.py`. Both routers are registered in `server.py`; new endpoints are documented in `SCHEMA.md`.

- [#1859](https://github.com/zealchaiwut/commander/issues/1859) API: read-only docs endpoints — list and fetch docs/**, README, CHANGELOG — 2026-07-13
- [#1861](https://github.com/zealchaiwut/commander/issues/1861) API: agent operate guide — GET /api/agent-guide with canonical workflow recipes — 2026-07-13

## Sprint 114.1

Single-bug follow-up to Sprint 114 — removes a dead per-project fetch/render path on Home. The milestone badge on Home project cards had been CSS-permanently hidden ("Milestones hidden per operator request"), yet every Home load still paid for it twice: the frontend `loadMilestone()` fired a fetch per project card, and the server-side `_enrich_home_artifact()` (`routers/brief.py`) still called `home_milestone_service.active_milestone_progress()`. **#1845** deletes both — the `loadMilestone()` JS function, its milestone `forEach` populate block, the `<a class="pb-milestone">` DOM stub in `renderProject()`, and the `.pb-milestone` CSS block are removed from `static/home.html`, and the milestone enrichment call is dropped from `_enrich_home_artifact`. Home now loads with zero milestone-related requests (verified by a Node fetch-spy behavioral test plus a Python spy asserting zero `active_milestone_progress()` calls on `/api/brief/daily`). No new endpoints or schema; the milestone badge stays hidden (revert this ticket and un-hide the CSS if it's ever wanted back).

- [#1845](https://github.com/zealchaiwut/commander/issues/1845) bug: Home fetches milestone data for a permanently-hidden badge — remove dead fetch/render — 2026-07-12

## Sprint 114

Dashboard reorganization pass over the Analytics, Logs, and Home surfaces — mostly frontend restructuring plus a handful of API/data-shape changes, with a parallel dead-code cleanup. **Analytics.** The Analytics view is promoted out of the Manage dropdown into a top-level tab and now lands on the **Metrics** sub-tab by default (#1856). The retired standalone `static/analytics.html` page (~1500 lines) and its serving route are deleted — `GET /project/{slug}/analytics` now returns a **permanent 301 redirect** to the in-chrome `/project/{slug}/metrics` tab, and the dead `_query_trend_30d()` helper plus the `trend_30d` key are removed from the `/api/projects/{slug}/analytics/cost` response (#1846). The Trends charts no longer mix all projects: `GET /api/metrics/sprints` gains an optional `project=<slug or owner/repo>` query param (matched by full `owner/repo` or bare slug) and the frontend passes the active project slug (#1843). `_compute_analytics_metrics` gains a per-sprint `by_sprint` trend series (each entry carries `sprint_label`, `first_pass_rate`, `rework_rate`, `avg_coder_minutes`, `wall_clock_minutes`, `tickets_done`) (#1848) and a `most_reworked` list (top 5 issues by tester rejections) that replaces the fictitious dollar-cost cards with a most-reworked-tickets list (#1852). The by-agent and by-ticket token tables are ported into the live Status sub-tab (#1851). **Logs.** The Activity feed is grouped by sprint run instead of calendar day (`static/src/activity-grouping.js`, #1853); an error-visibility pass adds a nav badge with the error count, an automatic error filter, and a since-last-visit fetch window (`static/src/logs-error-badge.js`, #1857); the Raw view auto-selects the latest run and hides toolbar controls irrelevant to the active view (`static/src/logs-view-controls.js`, #1858). The Runs (Timeline) view is removed and redirects to Sprint History (#1850), and the unreachable Logs Search/Issues views plus dead toolbar branches are deleted (#1847). **Home.** Failure-first triage sorts failed items first, shows a red **Failed** KPI, promotes the status line, and collapses the shipped detail by default (#1855). The home daily-brief artifact is now invalidated on sprint lifecycle changes — sprint start/finish and ticket label transitions (→SIT, →UAT, →done) best-effort delete today's brief so the next `GET` regenerates it (via `apps/dashboard/routers/brief_invalidation.py` + a fire-and-forget hook in `services/sprint_manager/state_machine.py`) — plus a manual **Refresh** button (#1854). **Sprint tab.** The Board/History header gains a delivery-health headline stat strip (`static/src/sprint-board/health-strip.js`, #1849). The frontend bundle (`static/dist/bundle.js`) is rebuilt and committed.

- [#1843](https://github.com/zealchaiwut/commander/issues/1843) bug: Analytics Trends charts mix all projects — add project= filter to /api/metrics/sprints — 2026-07-12
- [#1846](https://github.com/zealchaiwut/commander/issues/1846) cleanup: delete retired analytics.html page, its route, and dead trend_30d query — 2026-07-12
- [#1847](https://github.com/zealchaiwut/commander/issues/1847) cleanup: delete unreachable Logs Search/Issues views and dead toolbar branches — 2026-07-12
- [#1848](https://github.com/zealchaiwut/commander/issues/1848) Analytics: per-sprint trend series — first-pass rate, rework rate, duration per sprint — 2026-07-12
- [#1849](https://github.com/zealchaiwut/commander/issues/1849) Sprint tab: delivery-health headline stat strip on Board/History header — 2026-07-12
- [#1850](https://github.com/zealchaiwut/commander/issues/1850) Logs: remove the Runs (Timeline) view — Sprint History already shows it better — 2026-07-12
- [#1851](https://github.com/zealchaiwut/commander/issues/1851) Analytics: port by-agent and by-ticket token tables into the live Status sub-tab — 2026-07-12
- [#1852](https://github.com/zealchaiwut/commander/issues/1852) Analytics: replace fictitious dollar-cost cards with most-reworked-tickets list — 2026-07-12
- [#1853](https://github.com/zealchaiwut/commander/issues/1853) Logs: group Activity feed by sprint run instead of calendar day — 2026-07-12
- [#1854](https://github.com/zealchaiwut/commander/issues/1854) Home: brief freshness — invalidate artifact on sprint lifecycle changes + manual Refresh button — 2026-07-12
- [#1855](https://github.com/zealchaiwut/commander/issues/1855) Home: failure-first triage — sort failures first, red Failed KPI, promote status line, collapse shipped detail — 2026-07-12
- [#1856](https://github.com/zealchaiwut/commander/issues/1856) Analytics: land on Metrics sub-tab and promote Analytics out of the Manage dropdown — 2026-07-12
- [#1857](https://github.com/zealchaiwut/commander/issues/1857) Logs: error visibility — nav badge, auto error filter, since-last-visit window — 2026-07-12
- [#1858](https://github.com/zealchaiwut/commander/issues/1858) Logs: Raw view auto-selects latest run; hide toolbar controls irrelevant to active view — 2026-07-12

## Sprint 107.1

Rerun-button robustness fix — completes the sprint-107 bug-fix pass. **Board Re-run button silently no-ops (#1754):** `smgmtRerunSprint` ran several unguarded DOM/helper calls (`getElementById(...).textContent=`, `_rrShowPreviewLoading` -> `renderProgressActivity`) before opening the re-run modal, with no try/catch around that setup; any exception there aborted the whole click handler silently — no modal, no toast, no dispatch — even though the equivalent `curl` to the same endpoint worked. `_rrOpen()` now runs first so the modal is guaranteed visible once the handler starts, the rest of the setup + preview fetch is wrapped in the existing try/catch, and a toast now fires alongside the inline error banner on preview-load failure, the `_rrConfirm` failure path, and when no project is loaded. No new schema, tables, columns, or endpoints.

- [#1754](https://github.com/zealchaiwut/commander/issues/1754) Board card 'Re-run -> N.1' button silently no-ops; API rerun works — 2026-07-12

## Sprint 113

Sprint-lifecycle correctness pass — four bug fixes to sprint creation and rerun/child-sprint dispatch, no new features, endpoints, or schema. **Stale-mirror label verify (#1822):** sprint creation with tickets always failed its post-create label check because `SprintCreateDeps.ticket_labels_applied` read labels from the SQLite `issues` mirror (up to 60 s stale), so freshly applied `sprint-N` labels weren't visible yet. Added `github_client.get_issue_live(issue_number, repo_name=)`, which fetches a single issue from live GitHub (`gh issue view --json ...`), bypassing the mirror; the verify path now uses it while all normal read paths stay on the zero-quota `get_issue()`. **Sprint-create error handler (#1823):** `POST` sprint-create caught `SprintCreationError` via `sprints_service.SprintCreationError`, but that alias no longer existed, so a legitimate creation error raised `AttributeError` and surfaced as a bare 500 with no detail; `routers/sprint_crud.py` now imports `SprintCreationError` from `services.sprint_manager.sprint_creation` directly and maps it to its intended `status_code`/`message`. **Rerun dispatch race (#1827):** on sprint rerun, the endpoint moved ticket labels and immediately spawned `sprint_manager`, which re-queried GitHub for the roster — but GitHub is eventually consistent and could still return the pre-move labels, so the new run dispatched a stale subset (or nothing). The rerun endpoint now writes a `{sub_label}-rerun-manifest.json` (the exact moved-decision list + `all_moved`) into the sprints dir and passes `--rerun-manifest <path>` to `sprint_manager`, which dispatches from the manifest instead of re-querying. As a safety net, when the manifest reports `all_moved=True` with decisions but 0 tickets are dispatchable, the preflight now emits an explicit dispatch **error** (listing the missing `#N`s) and sets `rerun_dispatch_error` on the preflight result instead of the old silent "all skipped" no-op. **Child-sprint branch base (#1828):** a child sub-sprint branch (`sprint/sprint-N.M`) was always cut off the root sprint branch, so a rerun of a deeper sub-sprint lost its immediate parent's commits. `_create_sprint_branch` gained a `fallback_ref` parameter; the preflight now resolves a child's `parent_ref` from the plan's immediate `parent` (via `_immediate_parent_branch`) and only falls back to the root base branch (with a WARN) when the parent branch can't be resolved on origin. Also: hoisted the `api_client` import to satisfy `noqa: E402` and de-aliased the `logs_service._slog` reference in `routers/logs.py`. No new schema, tables, columns, or endpoints.

- [#1822](https://github.com/zealchaiwut/commander/issues/1822) Sprint create with tickets always fails: label verify reads stale issues mirror — 2026-07-10
- [#1823](https://github.com/zealchaiwut/commander/issues/1823) Sprint create error handler raises AttributeError — SprintCreationError surfaced as bare 500 — 2026-07-10
- [#1827](https://github.com/zealchaiwut/commander/issues/1827) Rerun label-move → sprint_manager dispatch race: freshly moved tickets missed by the new run — 2026-07-10
- [#1828](https://github.com/zealchaiwut/commander/issues/1828) Child sub-sprint branches off root sprint branch, not its parent sub-sprint — reruns lose the parent's commits — 2026-07-10

## Sprint 112.1

Aggregate-cutover and call-budget enforcement pass — completes Phase 3 of `docs/milestones/board-aggregate-api.md` and locks in the call-reduction work with a shared test harness. **Aggregate-flag cutover (#1789):** the three provisional feature flags introduced in #1638/#1640/#1646 (`COMMANDER_BOARD_AGGREGATE`, `COMMANDER_HISTORY_AGGREGATE`, `COMMANDER_RUNNING_AGGREGATE`) are removed entirely — their helper functions (`board_aggregate_enabled`, `history_aggregate_enabled`, `running_aggregate_enabled`) and the corresponding `board_aggregate`/`history_aggregate`/`running_aggregate` keys in `commander_features()` (`/api/environment`) are deleted from `config.py`, and the legacy per-card/per-agent fan-out fallback paths in `board-render.js`, `history.js`, and the inline running view are deleted. The aggregate endpoints (`/api/sprints/{label}/board`, inline history `run_stats`, consolidated `/api/running`) are now the only path; the bundle is rebuilt and committed. All three flag names are grep-clean across the repo. **Shared call-count-budget harness (#1788):** a reusable pytest fixture (`tests/test_call_budgets.py`) plus a frontend patched-fetch counter (`tests/frontend/call-budget-helpers.mjs`) encode the call-budget arc targets — board load = 1 aggregate call, home load ≤ 4, history feed = 1, running first paint = 1, and zero `gh` subprocess calls per aggregate request — so any future feature that re-introduces a fetch loop fails a budget test instead of silently regressing the work. Budgets blocked on unshipped tickets use `@pytest.mark.xfail` and flip to enforced automatically once those land. Usage is documented in `tests/README.md`.

- [#1788](https://github.com/zealchaiwut/commander/issues/1788) Add shared call-count-budget test harness — 2026-07-09
- [#1789](https://github.com/zealchaiwut/commander/issues/1789) Cut over aggregate flags and delete legacy fan-out paths — 2026-07-09
## Sprint 112

Cache-observability and live-refresh pass — the dashboard now pushes board-cache invalidations to clients over SSE and exposes in-process call-volume/cache-hit counters for tuning. **SSE board invalidation (#1785):** `invalidate_board()` (`routers/board_cache.py`) now, in addition to evicting the project's cache entry, broadcasts a `{"type": "board_invalidated", "project": ...}` message over the existing SSE stream (skipped cleanly in sync/no-event-loop test contexts). The issues-mirror sync loop (`github_events_sync.py`) calls `invalidate_board(repo)` only when a mirror sync returns HTTP 200 (rows actually changed) — a 304 no-change poll broadcasts nothing. The project page (`static/project.html`) handles the event via `_boardSseOnInvalidated`, and a new `visibilitychange` handler defers the refetch (`_boardSseOnVisible`) when a `board_invalidated` arrives while the tab is hidden. **API call-volume + cache-hit observability (#1787):** a new in-process counters module (`apps/dashboard/api_volume.py`) tracks per-normalized-path request volume (via a new `server.py` HTTP middleware that reads the matched route template, e.g. `/api/sprints/{label}/live`), github_client cache hit/miss, issues-mirror live-fallback count, board-cache hit/miss, and total `gh` subprocess invocations — all module-level integers that reset on process restart (no persistence, no locks beyond the GIL). Exposed at `GET /api/debug/api-volume?n=20` (`routers/api_volume.py`) as a self-documenting snapshot with per-cache hit rates. **Cache inventory + invalidation-contract docs (#1790):** `docs/architecture/1_state-and-source-of-truth.md` gains a cache-layer inventory and invalidation-contract table documenting each in-memory cache (github_client TTL cache, issues mirror, board snapshot cache), its TTL, and what invalidates it.

- [#1785](https://github.com/zealchaiwut/commander/issues/1785) Broadcast board_invalidated over SSE for live client refresh — 2026-07-09
- [#1787](https://github.com/zealchaiwut/commander/issues/1787) Add API call-volume and cache-hit observability endpoint — 2026-07-09
- [#1790](https://github.com/zealchaiwut/commander/issues/1790) Add cache inventory and invalidation-contract table to architecture docs — 2026-07-09

## Sprint 111

GitHub-quota reduction pass — every read that could be served from the local `issues` mirror was moved off live `gh`/GraphQL calls, with no new features, endpoints, or schema (the `issues` mirror table already existed and is documented in `SCHEMA.md`). Analytics routers: `routers/metrics.py`, `routers/mis_sizing.py`, `routers/estimates.py`, and `routers/sprint_summaries.py` now derive UAT-labelled numbers and per-sprint needs-rework counts from `db.get_mirrored_issues(repo)` (a single O(n) mirror pass, cached per repo) and only fall back to `gh issue list` when the mirror is empty (#1780). Reconciliation: `reconcile_*` reads the ticket roster and summary issues from the mirror via an injectable `mirror_reader` (defaulting to `github_client._mirror_issues`), cutting a per-sprint fan-out of `gh issue list` + per-ticket `gh issue view` down to ≤1 gh call — the ≤60 s mirror staleness is acceptable for reconcile, and an empty mirror still triggers the original subprocess path (#1781). Dispatch: `dispatch._mirror_issue_body` serves issue bodies from `github_client._mirror_issue` at zero quota cost, with a stale-hit guard (records older than the start-of-dispatch mirror sync are refetched) and a live `gh api` fallback on mirror miss / missing body (#1782). `latest_active_sprint` / `group_issues_by_sprint` and `_get_issue_labels` (`label_transitions.py`, `github_client.py`) moved off GraphQL — the sprint grouping is now a single mirror pass and label re-fetches use REST, not GraphQL, when the mirror can't answer (#1783). Milestones: `github_client.list_milestones` is consolidated into one mirror-backed definition (`db.get_mirrored_milestones`, synced every 60 s) with a REST-paginate fallback, and per-PR/PR-head lookups gained a 30 s TTL cache (keyed `pr:{repo}:{number}` / `pr_head:{repo}:{branch}`, invalidated on PR mutations) so reconcile no longer re-runs `gh pr list` per sprint (#1784). No new schema, tables, columns, or endpoints.

- [#1780](https://github.com/zealchaiwut/commander/issues/1780) Route analytics routers through issues mirror — 2026-07-09
- [#1781](https://github.com/zealchaiwut/commander/issues/1781) Read reconcile roster from mirror, cut calls to ≤1 — 2026-07-09
- [#1782](https://github.com/zealchaiwut/commander/issues/1782) Read issue bodies from mirror instead of per-ticket gh api fetches — 2026-07-09
- [#1783](https://github.com/zealchaiwut/commander/issues/1783) Move latest_active_sprint and label reads off GraphQL — 2026-07-09
- [#1784](https://github.com/zealchaiwut/commander/issues/1784) Back list_milestones with mirror; cache PR lookups — 2026-07-09

## Sprint 110

Dashboard polling-cost reduction pass — five performance tickets that cut idle and steady-state network traffic without changing what the UI shows. **Tab-hidden polling pause (#1775):** a shared visibility guard (`static/src/shell/visibility.js`, exported globally as `visibilityInterval`, with a standalone `static/visibility.js` for non-bundle pages) wraps every `setInterval` poller so it clears when `document.hidden` is true and restarts on `visibilitychange` back to visible — firing once immediately on return so the UI catches up without waiting a full cycle. All pollers across `project.html` (6 timers: 2 s live-board, 30 s sidebar, 30 s home, 60 s status-tab, 15 s logs, 5 s inspector), `home.html`, `home-preview.html`, and `diagnostics.html` are wrapped; SSE `EventSource` connections are left open and untouched. **SSE-driven board (#1777):** the sprint board, inspector log pane, and per-worker lane tails now consume the existing per-sprint SSE stream (`/api/sprints/{label}/live/stream`) instead of the 2 s `/api/sprint-status` + `/api/sprints/{label}/live` REST poll — dropping steady-state polling from ~60 calls/min to ≤ 4. The stream (`routers/sprint_live.py`) emits a new `snapshot` event (full live-snapshot JSON on connect and every ~5 s / 10 half-second ticks) so the board bootstraps and refreshes metrics without a separate REST call; on disconnect it falls back to the sprint-103 `/api/running` slow poll (≥ 15 s) and resumes SSE automatically on reconnect. **Batched home fan-out (#1778):** `/api/brief/daily` now enriches each project entry with `repo`, `name`, `icon`, `color`, `briefSummary`, and `milestone`, and a new batch endpoint `GET /api/todos?projects=a,b,c` returns a slug-keyed `{slug: Todo[]}` map (unknown slugs → empty list); `CommanderTodo.mount` accepts an optional `preloadedData` argument to skip its per-project fetch, and the project page drops its duplicate `/api/projects` prefetch. Home-page load drops from 17 requests (5-project workspace) to 3–4 regardless of project count. Per-project endpoints (`/api/projects/{slug}/brief/summary`, `/api/home/milestone`, `/api/projects/{slug}/todos`) remain intact for the single-project page. **Refresh-loop dedup (#1776):** the project page's two overlapping 30 s `setInterval` timers are merged into one, the dead `/api/sprints/running-all` fetch (whose response was discarded) is removed, and `_snavRefreshAll()` now fires once per window instead of twice — deduped via a shared short-TTL snav cache (`static/src/shell/snav-cache.js`) so `/api/sprint-nav-status` for the current repo is fetched at most once per 30 s regardless of open tab; `/api/home` also refreshes immediately on `sprint_finished` / `sprint_reconciled` SSE events. Idle project-page call rate drops from ~26/min to ≤ 10/min. **Gh-auth poll + config cache (#1779):** the device-login status poller (`_gssGhAuthPollTimer`, `static/src/device-login.js`) slows from 600 ms to 2 s, and `/api/environment`, `/api/version`, and `/api/settings` are memoized as module-level promises (`static/src/api.js`) so each is fetched at most once per page session — the settings memo is invalidated and re-fetched after a successful `PUT /api/settings`; the memo is in-memory only (resets on full reload, never persisted). Also hardens `PUT /api/settings` with integer-type validation on int-typed fields.

- [#1775](https://github.com/zealchaiwut/commander/issues/1775) Pause dashboard polling when tab is hidden — 2026-07-09
- [#1776](https://github.com/zealchaiwut/commander/issues/1776) Deduplicate 30s refresh loops and remove dead running-all fetch — 2026-07-09
- [#1777](https://github.com/zealchaiwut/commander/issues/1777) Drive sprint board from SSE stream; drop snapshot polling — 2026-07-09
- [#1778](https://github.com/zealchaiwut/commander/issues/1778) Batch home-page API fan-out: 17 requests → 3-4 — 2026-07-09
- [#1779](https://github.com/zealchaiwut/commander/issues/1779) Reduce gh-auth poll frequency and cache stable config endpoints — 2026-07-09

## Sprint 109

Dead-code and one-shot-script cleanup pass — no new features, endpoints, or schema. Orphaned build tooling: the broken, unreferenced `scripts/build_frontend_bundle.py` (superseded by the esbuild `npm run build` step) is deleted (#1770). Dead API surface: three endpoints and their supporting code are removed — `DELETE /api/alerts/{idx}` (from `routers/system_misc.py`), `POST /api/projects/{owner}/{repo_name}/backlog/cleanup` (from `routers/tickets.py`), and `POST /api/reports/daily` along with the entire `routers/reports.py` module (unregistered from `routers/__init__.py` and `server.py`) (#1772). Lifecycle-state correctness: `_sprint_signoff_set_approved` in `startup.py` no longer writes the deprecated `planned` state on approval — it now writes the sanctioned post-#1686 `draft` value, removing the last emitter of `planned` (#1773). Script housekeeping: five landed one-shot scripts (`backfill_sprint_summary_label.py`, `batch_approve.py`, `clean_sprint_issues_json.py`, `migrate_add_agents_clone.py`, `migrate_attachments_to_branch.py`) are moved into a new `scripts/archive/` directory with a `README.md` documenting the archive's no-maintenance status (#1774). Docs: `CLAUDE.md` gains a Useful Scripts entry for `scripts/resync_issues_mirror.py`. No new schema, tables, columns, or endpoints.

- [#1770](https://github.com/zealchaiwut/commander/issues/1770) Delete orphaned and broken build_frontend_bundle.py — 2026-07-09
- [#1772](https://github.com/zealchaiwut/commander/issues/1772) Remove three dead API endpoints and orphaned helpers — 2026-07-09
- [#1773](https://github.com/zealchaiwut/commander/issues/1773) Remove last writer of deprecated 'planned' lifecycle state — 2026-07-09
- [#1774](https://github.com/zealchaiwut/commander/issues/1774) Archive six orphaned one-shot scripts from scripts/ — 2026-07-09

## Sprint 108

Mobile-responsive fix pass over the project dashboard (`apps/dashboard/static/project.html` + `static/src/shell/tabs.js`) — no new features, endpoints, or schema; all CSS/JS layout fixes for narrow viewports (≤430px). Sub-tab strip: at 390px the strip (~410px) overflowed the viewport, so `.sub-tabs` now scrolls horizontally with the scrollbar hidden; to keep the stab dropdowns from being clipped by the new scroll container, `.stab-group` drops `position: relative` (moved to `.sub-tabs-row`) and `toggleStabDropdown` re-positions the open dropdown as `position: fixed` off the trigger's viewport rect at ≤430px (reset on close) (#1765). Modal height: the modal is capped at `calc(100dvh - 32px)` with `overflow: hidden`, and `.modal-body` becomes the sole scroll region (`flex: 1 1 auto; overflow-y: auto; min-height: 0`) so tall modals no longer overflow off-screen; the "Move to" / history-split modals switch from fixed min/max widths to `width: min(<max>, calc(100vw - 32px))` so they fit the viewport (#1766). Horizontal overflow: `body { overflow-x: hidden }` clamps page scrollWidth to the viewport, the subnav pill row and sprint-header right cluster wrap, and the create/auto-refresh action cluster is allowed to shrink at ≤430px (#1768). Deploy tab: the multi-column `.deploy-grid` collapses to a single column, card action buttons stack full-width with a ≥44px touch target, and the inline confirm dialog stacks so both buttons fit without clipping at ≤430px (#1769). No new schema, tables, columns, or endpoints.

- [#1765](https://github.com/zealchaiwut/commander/issues/1765) Fix sub-tab strip scroll on mobile (390px) — 2026-07-08
- [#1766](https://github.com/zealchaiwut/commander/issues/1766) Cap modal height to prevent off-screen overflow on mobile — 2026-07-09
- [#1768](https://github.com/zealchaiwut/commander/issues/1768) Fix horizontal overflow on mobile viewports ≤430px — 2026-07-09
- [#1769](https://github.com/zealchaiwut/commander/issues/1769) Fix stuck Deploy loader and mobile-responsive cards at 430px — 2026-07-09
## Sprint 107

Board bug-fix pass over the "Move to Sprint" flow and backlog bulk-select — no new features, endpoints, or schema. **Move to Sprint → Create new sprint** no longer silently no-ops when the next sprint label already exists: `_smgmtMoveModalPick` / `_smgmtMoveModalPickBulk` (`project.html`) stop sending a client-computed `sprint_number` on create and instead let `POST /api/sprints/create` assign the next free number, reading the real `sprint_label` back from the response; a create that fails now throws instead of swallowing the 409, and the optimistic row move is skipped for the isNew case since the target label is unknown until the server replies. `_smgmtGhostComputeNextFree` now prefers the board API's authoritative `placeholder_sprint` and folds finished-sprint labels (`_smgmtFinishedLabels`) into its fallback max so it can't pick a stale number. `sprint_crud.py` imports `SprintCreationError` from its canonical module so the create-label error path resolves correctly (#1747). **Backlog bulk-select** state and clicks are no longer lost to the board auto-refresh re-render: `_smgmtRenderBacklog` (`board-render.js`) skips the ticket-list DOM replacement while a selection is active (`_smgmtSelectedIssues.size > 0`) so a mid-click node swap can't swallow the click or orphan checked boxes, and it now calls `_smgmtUpdateSelectionUI()` after every render (including suppressed ones) so the top selection bar count and visibility always reflect the live JS Set. The selection action bar is also re-anchored `position: fixed` at the bottom-center of the viewport so it never shifts the board layout when it appears (#1748). No new schema, tables, columns, or endpoints.

- [#1747](https://github.com/zealchaiwut/commander/issues/1747) Move to Sprint -> Create new sprint silently no-ops when next sprint label already exists — 2026-07-06
- [#1748](https://github.com/zealchaiwut/commander/issues/1748) Backlog bulk-select state lost and clicks swallowed by board auto-refresh re-render — 2026-07-06

## Sprint 106.1

Follow-up hardening pass over the sprint-106 board work — no new features, endpoints, or schema. Board-cache correctness: `invalidate_board(...)` is now called from every remaining board-mutating endpoint so a stale `/api/board` snapshot can't survive a mutation — issue approve/reject/close (`routers/issues.py`), sprint ticket reorder / plan save / empty-sprint delete+cleanup (`routers/sprint_crud.py`), sprint finish + step complete (`routers/sprint_finish.py`), split-XL apply + clear-stale-labels (`routers/sprint_history.py`), and sprint order save / plan-next (`routers/sprints.py`) (#1743). Live-cache race fix: `_smgmtLiveCache` gains a stale-write guard (monotonic `_smgmtLiveCacheSeq` + per-label `_smgmtLiveCacheWriteSeq`) so a slow first-paint fetch (`_smgmtRunningFirstPaint`) can no longer overwrite a fresher poll-tick result (`_smgmtLivePollTick`) — each writer claims a seq before its fetch and skips the store if a newer seq already won (#1745). Test-quality guardrail: AC tests must exercise behavior, not source text — added the rule to `CLAUDE.md` and converted the sprint-101–103 regex/source-match tests to behavioral ones (`TestClient`/mock-boundary Python tests, plus Node `--test` `fetch`-spy and SSE frame-through-parser suites under `tests/frontend/`) (#1746). No new schema, tables, columns, or endpoints.

- [#1743](https://github.com/zealchaiwut/commander/issues/1743) Wire invalidate_board into remaining board-mutating endpoints — 2026-07-06
- [#1745](https://github.com/zealchaiwut/commander/issues/1745) Guard _smgmtLiveCache against stale overwrites (first-paint vs poll race) — 2026-07-06
- [#1746](https://github.com/zealchaiwut/commander/issues/1746) Replace source-regex tests with behavioral tests for sprints 101-103 features — 2026-07-06

## Sprint 106

Follow-up cleanup pass — one board-load optimization plus test housekeeping. Board aggregate: `_build_sprint_card` in `routers/board_service.py` now inlines the last per-sprint fan-out fields — sprint goal, outcome, conflict counts, finish-card, and branch status — directly into each `/api/board` card via new zero-`gh`/zero-subprocess helpers (`_read_sprint_goal`, `_build_conflicts_inline`, `_build_outcome_inline`, `_build_finish_card_inline`, `_build_branch_status_inline`, all sourced from the DB and disk). With `COMMANDER_BOARD_AGGREGATE=1`, `board-render.js`'s `_smgmtFetchMissingOutcomes` / `_smgmtLoadGoals` / `_smgmtLoadConflicts` / `_smgmtLoadFinishCards` loops consume the inline data and make zero per-sprint requests, so a board load is a single `/api/board` call; with the flag OFF, behavior is unchanged (branch status in the aggregate path reports `exists=False` since it skips the `gh` existence check) (#1744). Test housekeeping: the duplicate test files shipped in sprint 101 for #998/#1652/#1653/#1676 are deduped into the convention-named `tests/test_<N>__<criterion>.py` variants (#1742), and two tests red on develop HEAD (a #1652 string-match assertion and a #1653 cwd dependency) are fixed to be behavioral rather than source-regex checks (#1741). No new schema, tables, columns, or endpoints.

- [#1741](https://github.com/zealchaiwut/commander/issues/1741) Fix 2 tests red on develop HEAD (1652 string-match, 1653 cwd) — 2026-07-06
- [#1742](https://github.com/zealchaiwut/commander/issues/1742) Dedupe duplicate test files shipped in sprint 101 (#998/#1652/#1653/#1676) — 2026-07-06
- [#1744](https://github.com/zealchaiwut/commander/issues/1744) Board aggregate flag: inline remaining per-sprint fan-out (outcomes, goals, conflicts, finish-cards) — 2026-07-06

## Sprint 101.9

Follow-up cleanup and hardening pass — no new features, endpoints, or schema. Frontend robustness: the preflight stepper now fails safe. `_pfStepperAnimate(data)` is called with a trailing `.catch(() => _pfUpdateConfirmBtn())` so a throw mid-animation always re-enables the Run button instead of leaving it permanently disabled; the autofix SSE fetch (`_pfRunAutoFix`) is wrapped in an `AbortController` that aborts after a 120s timeout (`AUTOFIX_TIMEOUT_MS`); and autofix outcomes are surfaced in the step notes — errored tickets append `(N could not be fixed)` and a timeout appends `(timed out)` rather than silently reporting a clean pass (#982). Backend/tooling cleanup: the redundant `git rebase --abort` in the `_call_finish_feature` rebase-conflict path is removed — the conflicted worktree is discarded by the caller anyway, so the extra abort was dead work (#1653); the mid-file `api_client` import in `sprint_manager.py` (`is_retryable_rate_limit`) is hoisted into the top-of-file import block for consistent import ordering (#1676). Test housekeeping: the duplicate design-token test file is consolidated into the convention-named `tests/test_1200__consolidate_design_tokens.py`, deleting the older `tests/test_consolidate_design_tokens__1200.py` (#1592).

- [#982](https://github.com/zealchaiwut/commander/issues/982) Preflight stepper hardening: catch _pfStepperAnimate, surface autofix errors, timeout the autofix fetch — 2026-07-06
- [#1592](https://github.com/zealchaiwut/commander/issues/1592) Remove duplicate test file for #1200 design-token consolidation — 2026-07-06
- [#1653](https://github.com/zealchaiwut/commander/issues/1653) _call_finish_feature: remove redundant rebase --abort in conflict path — 2026-07-06
- [#1676](https://github.com/zealchaiwut/commander/issues/1676) Move mid-file api_client import to top-of-file imports in sprint_manager.py — 2026-07-06

## Sprint 105

ICA-provider awareness pass. Agent roles can now route to a different LLM provider per sprint: `sprint.yaml` `agent_config.coder.profile` / `agent_config.tester.profile` are loaded onto `SprintConfig` and, via the new `get_role_profile()` helper, exported as `CCPROXY_PROFILE` in each role's dispatch subprocess — leaving the variable unset (so the inherited global wins) when no per-role profile is configured (#1671). To make the cost trade-off visible, the coder's resolved profile is captured as `coder_provider` on `IssueState`/the live snapshot and persisted to a new `agent_runs.provider` column at dispatch; when the provider is `ICA`, the live board (`project.html`) and run browser (`run_browser.html`) render a yellow **`caching: reduced`** badge with a tooltip explaining that the ICA proxy strips prompt-caching headers and re-sends full context each turn (#1673). The `/api/runs` payload and `get_sprint_live_snapshot` now surface `provider` / `coder_provider` accordingly.

- [#1671](https://github.com/zealchaiwut/commander/issues/1671) Add per-role LLM provider routing via profile override — 2026-07-01
- [#1673](https://github.com/zealchaiwut/commander/issues/1673) Show 'caching: reduced' indicator when provider is ICA — 2026-07-01

## Sprint 104

LLM-provider support pass: a global Anthropic/ICA backend toggle, broader rate-limit detection, and run-view provider badges. A new global setting `llmProvider` (`anthropic` | `ica`, default `anthropic`) gates which backend all newly dispatched agents use; it is switched only through the dedicated `POST /api/settings/provider` endpoint, which instructs the local claude-proxy to activate the matching profile (`POST {COMMANDER_PROXY_URL}/profile`, default `http://localhost:9090`) and persists the selection only after the proxy confirms — an unreachable or non-2xx proxy returns HTTP 503 with no silent fallback. `PUT /api/settings` now rejects `llmProvider` (HTTP 422) so the field can only change via the proxy-aware path. The Global Settings screen gains an "LLM Provider" card with Anthropic/ICA buttons and inline switch/error status. In-flight agent sessions keep their existing proxy connections; only the next dispatched agent inherits the change (#1667). Rate-limit/quota detection is extracted into `services/sprint_manager/api_client.py` (`is_retryable_rate_limit`) and extended to recognise ICA/IBM Cloud gateway shapes (`quota_exceeded`, `token_limit_exceeded`, `usage_limit_exceeded`, …) alongside the existing Anthropic OAuth 429 signals; both provider paths funnel into the same retry/backoff logic with no caller changes (#1669). The active provider at sprint start is captured on `SprintState.llm_provider`, persisted in `state.json`, and surfaced in the live snapshot (`llm_provider`) so the running header and per-ticket rows render a provider badge reflecting the backend actually in use for that run, not the current global setting (#1670).

- [#1667](https://github.com/zealchaiwut/commander/issues/1667) Add global LLM provider toggle (Anthropic/ICA) to Commander settings — 2026-06-30
- [#1669](https://github.com/zealchaiwut/commander/issues/1669) Extend rate-limit detection to handle ICA/IBM gateway error formats — 2026-06-30
- [#1670](https://github.com/zealchaiwut/commander/issues/1670) Show LLM backend badge on dashboard run views — 2026-06-30

## Sprint 97.2

Follow-up hardening pass (re-run of sprint-97.1/97) covering sprint-manager concurrency, subprocess robustness, the issues mirror, and a few dashboard UI fixes. Concurrency and process safety: `SprintState.save` now serializes writes under a `threading.Lock` and persists atomically via `os.replace()` so concurrent-pipeline runs can't interleave or corrupt `state.json` (#776), and dual active-agent cards now read per-issue `coder_pid`/`tester_pid` instead of a shared field so each card shows the correct PID (#777). Subprocess calls are bounded: deploy/restart helpers (#747) and the stale-branch scan/cleanup `gh` calls (#849) now pass explicit timeouts so a hung child can't wedge the pipeline. Sprint-state correctness: `RUN_MUTABLE_LABELS` now includes `blocked` by importing the canonical constant from `state_machine` (with `_apply_in_progress_label`/`_apply_needs_rework_label` helpers) so a blocked ticket stays mutable mid-run (#814). Issues mirror: `_fetch_issues_conditional` follows the `Link` rel="next" pagination header, fixing the 100-issue cap that silently truncated the mirror (#815). Events/activity API: `agent_runs` duration enrichment is scoped by `sprint_label` so durations no longer bleed across sprints (#820). Deploy config: `GET/PUT /api/projects/{slug}/deploy-config` gains a seed-only-project fallback so newly-seeded projects resolve config instead of 404-ing (#746). Frontend CI commits `package-lock.json` and switches from `npm install` to `npm ci` for reproducible builds (#837). Tooling: `doctor.py` now probes Claude CLI auth via a `-p` empty-prompt call rather than only `--version`, catching authenticated-but-broken setups (#868). Dashboard UI: the History loose-end band and Details now surface all failed reconciliation checks instead of dropping non-`stale_labels` failures (#1154), and ancestor expand/collapse state is restored from `localStorage` on render so it survives reloads (#1155). No new schema or endpoints — internal correctness, robustness, and UI fixes only.

- [#746](https://github.com/zealchaiwut/commander/issues/746) Add seed-only-project fallback to GET/PUT deploy-config — 2026-06-25
- [#747](https://github.com/zealchaiwut/commander/issues/747) Add timeouts to deploy/restart subprocess calls — 2026-06-25
- [#776](https://github.com/zealchaiwut/commander/issues/776) Serialize SprintState.save under concurrent pipeline mode — 2026-06-25
- [#777](https://github.com/zealchaiwut/commander/issues/777) Per-issue coder_pid/tester_pid fixes shared PID on dual agent cards — 2026-06-25
- [#814](https://github.com/zealchaiwut/commander/issues/814) Add "blocked" to RUN_MUTABLE_LABELS via state_machine constant — 2026-06-25
- [#815](https://github.com/zealchaiwut/commander/issues/815) Follow Link pagination in issues-mirror sync (fix 100-issue cap) — 2026-06-25
- [#820](https://github.com/zealchaiwut/commander/issues/820) Scope agent_runs duration enrichment by sprint_label in events API — 2026-06-25
- [#837](https://github.com/zealchaiwut/commander/issues/837) Commit package-lock.json and use npm ci in frontend CI — 2026-06-25
- [#849](https://github.com/zealchaiwut/commander/issues/849) Add timeout to gh subprocess calls in stale-branch scan/cleanup — 2026-06-25
- [#868](https://github.com/zealchaiwut/commander/issues/868) doctor.py claude check: probe auth, not just --version — 2026-06-25
- [#1154](https://github.com/zealchaiwut/commander/issues/1154) Surface all failed reconciliation checks in loose-end band and Details — 2026-06-25
- [#1155](https://github.com/zealchaiwut/commander/issues/1155) Restore ancestor expand/collapse state from localStorage on render — 2026-06-25
## Sprint 99.1

Concurrent-sprint hardening pass. A `ConcurrentStateGuard` (`services/sprint_manager/concurrent_scheduler.py`) wraps all worker-thread read-modify-writes on `SprintState` token counters and `state.save()` calls behind a single lock when `max_coder_slots > 1`, preventing counter drops and interleaved file writes under parallel execution (#1436). A `tester_worktree_guard` context manager (`serialization.py`) serializes every tester task's checkout/rebase/pytest/merge sequence against the single shared tester worktree clone, stopping concurrent tester tasks from colliding on `git index.lock`, crossing feature branches, and cross-contaminating pytest output (#1438). `max_coder_slots` is now clamped to the hard cap of 4 in `load_config` so the worker-thread count and worktree-pool size stay aligned (#1437). Worktree pool no longer re-enqueues a failed slot into the free pool after setup failure, preventing subsequent tasks from drawing a broken slot (#1435). Rebase recovery's rollback path now re-checks out the `develop` branch before releasing the tester worktree, ensuring the clone is not left stranded on a feature branch after a failed rebase (#1439). The live snapshot now carries `active_coder_slots` and `active_tester_slots` alongside the capacity values, wiring up the previously inert multi-lane board view (#1440). Split confirm adds best-effort rollback compensation on partial failure: if a child issue creation fails mid-split, already-created children are closed on GitHub and the response surfaces which numbers need manual cleanup (#1453). The `strip_sprint_label` helper now removes SIT and UAT workflow-state labels in addition to in-progress/backlog ones, preventing stale labels from leaking onto child tickets after a split (#1454). `dispatch._fetch_dispatch_issue_body` is extracted into its own helper that returns an empty-string sentinel on failure (with a WARN log) so `_build_design_block` skips its own fallback fetch instead of silently issuing a second round-trip per ticket (#1573). `repair_sprint_collisions.py`'s `ASSERT_ABSENT` check is scoped to `(label, project)` so a label present in another project does not incorrectly block repair (#1481). Dead `_apply_in_progress_label`/`_apply_needs_rework_label` helpers are removed from `dispatch.py` (#1585). `doctor.py`'s Claude CLI check is split into two gates — a fast binary-reachability probe (`claude --version`) and a documented auth probe (`claude -p "" --output-format json`) that makes a real minimal inference call; the check name and cost note are surfaced to the operator (#1586). The unreachable `except subprocess.TimeoutExpired` handler in `_is_merged` is removed (#1587). `history.js` exports seven previously file-local render helpers (`_histVerbsHtml`, `_histHeadLinksHtml`, `_histHeadActionsHtml`, `_histMetricsHtml`, `_histGanttHtml`, `_histHeadHintsHtml`, `_histPartition`) so they are testable without DOM side effects, and the stale `_smgmtNextUpLabel` global-directive entry is removed from `board-render.js` (#1588, #1574). `smgmtFixOrderViolation` is converted from an async backend-writing function to a synchronous DOM-only reorder, matching the UI-only scope declared by #1422 (#1446). `parse_files_to_touch` now tracks multi-line HTML comment state so lines inside `<!-- … -->` blocks that span multiple lines are not counted as touched files (#1429).

- [#1429](https://github.com/zealchaiwut/commander/issues/1429) parse_files_to_touch mishandles multi-line HTML comments — 2026-06-29
- [#1435](https://github.com/zealchaiwut/commander/issues/1435) Worktree pool enqueues failed slots into the free pool — 2026-06-29
- [#1436](https://github.com/zealchaiwut/commander/issues/1436) Concurrent scheduler mutates shared SprintState without a lock — 2026-06-29
- [#1437](https://github.com/zealchaiwut/commander/issues/1437) max_coder_slots not clamped to hard cap 4 in load_config — 2026-06-29
- [#1438](https://github.com/zealchaiwut/commander/issues/1438) Concurrent test tasks share a single tester worktree — 2026-06-29
- [#1439](https://github.com/zealchaiwut/commander/issues/1439) Rebase recovery leaves tester worktree on a feature branch — 2026-06-29
- [#1440](https://github.com/zealchaiwut/commander/issues/1440) Multi-lane view inert — live snapshot omits max_coder_slots — 2026-06-29
- [#1446](https://github.com/zealchaiwut/commander/issues/1446) Fix order persists to backend despite #1422 UI-only scope — 2026-06-29
- [#1453](https://github.com/zealchaiwut/commander/issues/1453) split confirm has no rollback on partial failure — 2026-06-29
- [#1454](https://github.com/zealchaiwut/commander/issues/1454) child label strip set omits SIT/UAT workflow-state labels — 2026-06-29
- [#1481](https://github.com/zealchaiwut/commander/issues/1481) Scope ASSERT_ABSENT lookup to (label, project) in repair script — 2026-06-29
- [#1573](https://github.com/zealchaiwut/commander/issues/1573) dispatch double-fetches issue body on gh failure path (silent swallow) — 2026-06-29
- [#1574](https://github.com/zealchaiwut/commander/issues/1574) Stale eslint /* global */ entry for now-local _smgmtNextUpLabel — 2026-06-29
- [#1585](https://github.com/zealchaiwut/commander/issues/1585) Remove or wire up dead _apply_in_progress_label/_apply_needs_rework_label helpers — 2026-06-29
- [#1586](https://github.com/zealchaiwut/commander/issues/1586) doctor.py claude auth probe performs a real inference round-trip on every run — 2026-06-29
- [#1587](https://github.com/zealchaiwut/commander/issues/1587) Unreachable except subprocess.TimeoutExpired in _is_merged — 2026-06-29
- [#1588](https://github.com/zealchaiwut/commander/issues/1588) history.js export/global-comment changes exceed #1154 loose-end-band scope — 2026-06-29

## Sprint 96

Follow-up hardening pass on Sprint 95's Definition-of-Ready plumbing plus assorted sprint-manager code-quality cleanups surfaced by code review. The board readiness check (`_smgmtReadinessCheck`) and the preflight AC detector now use the same anchored heading regexes as the canonical `parse_ticket_spec` (`^#{1,6}\s+…\s*$`), eliminating drift between the frontend and backend parsers, and the board readiness check gains a "missing design ref" reason so DoR coverage matches the Design Refs requirement (#1538, #1539, #1545). Design-ref handling is tightened: slugs are normalized via `_slugify_heading` before lookup so headings with punctuation/casing resolve correctly (#1543), `_build_design_block` now logs a WARNING instead of silently swallowing `gh`-fetch failures (#1540), and coder dispatch fetches the issue body once and passes it into `_build_design_block`, dropping the redundant per-dispatch `gh api` call (#1541). `lint_ticket_spec.py` now exits cleanly with a stderr message instead of a traceback when the `gh` API call fails (#1537). Several silent-drift and robustness fixes: the duplicated `FailureCategory` class in `pipeline.py`/`dispatch.py` is replaced by a single import from `failures.py` (#1511), the `_git_worktree_root`/`_parse_dotenv_value`/`_find_crg_bin` worktree helpers are deduplicated so `worktree.py` is the sole source of truth (#1502), `gates._lookup_in_sm` compares functions by code origin so `importlib.reload(gates)` no longer triggers infinite recursion in cross-test runs (#1500), `children_of` logs a warning on the label-only fallback path (#1498), and the bare `except` in `audit_sprint_collisions._collect_label_projects` is narrowed to `sqlite3.OperationalError` (#1494). No schema, endpoint, or behavior-contract changes — internal correctness and consistency only.

- [#1494](https://github.com/zealchaiwut/commander/issues/1494) Narrow bare except in audit_sprint_collisions._collect_label_projects — 2026-06-24
- [#1498](https://github.com/zealchaiwut/commander/issues/1498) children_of label-only fallback emits warning (AC#4 gap) — 2026-06-24
- [#1500](https://github.com/zealchaiwut/commander/issues/1500) Fix gates.py proxy identity check under importlib.reload — 2026-06-24
- [#1502](https://github.com/zealchaiwut/commander/issues/1502) Deduplicate worktree helper copies into worktree.py — 2026-06-24
- [#1511](https://github.com/zealchaiwut/commander/issues/1511) Dedupe FailureCategory: import from failures.py in pipeline.py & dispatch.py — 2026-06-24
- [#1537](https://github.com/zealchaiwut/commander/issues/1537) Handle gh API failure in lint_ticket_spec.py without traceback — 2026-06-24
- [#1538](https://github.com/zealchaiwut/commander/issues/1538) Add 'missing design ref' reason to DoR readiness check — 2026-06-24
- [#1539](https://github.com/zealchaiwut/commander/issues/1539) Align board readiness heading patterns with canonical parser — 2026-06-24
- [#1540](https://github.com/zealchaiwut/commander/issues/1540) Log WARNING on gh-fetch failure in _build_design_block — 2026-06-24
- [#1541](https://github.com/zealchaiwut/commander/issues/1541) Avoid redundant gh API call per dispatch in _build_design_block — 2026-06-24
- [#1543](https://github.com/zealchaiwut/commander/issues/1543) Normalize design ref slug via _slugify_heading before lookup — 2026-06-25
- [#1544](https://github.com/zealchaiwut/commander/issues/1544) Isolate unrelated lint refactors from board readiness work — 2026-06-25
- [#1545](https://github.com/zealchaiwut/commander/issues/1545) Align frontend AC-detection regex with canonical parser — 2026-06-25

## Sprint 95

Definition-of-Ready (DoR) plumbing end to end. A single canonical ticket-spec parser (`services/sprint_manager/ticket_spec.py`, `parse_ticket_spec`) becomes the one source of truth for pulling Acceptance Criteria, Design Refs, Test Plan / UAT Steps, and Out of Scope out of an issue body — heading-synonym aware and never-raising — and the preflight router's hand-rolled `"## Acceptance Criteria" in body` check is replaced by it (#1485). A `scripts/lint_ticket_spec.py` CLI reports each section present/missing for an issue (exit 1 if any missing). The Planning board now renders a per-ticket ✓/✗ readiness badge (`_smgmtReadinessCheck`: missing AC, missing test plan, missing estimate, XL-split-required) and gates the Run Sprint button by DoR mode — `block`, `warn`, or `off` — driven by `COMMANDER_DOR_MODE` / the `definition_of_ready_mode` global setting and surfaced to the UI in `/api/environment` (#1487). At coder dispatch the sprint manager injects a DESIGN.md context block built from the ticket's `## Design Refs` (each `DESIGN.md#slug` resolved to its section text, capped at 6000 chars; a compact heading index is injected when no Design Refs are present), warning on unresolved slugs (#1488). The BA agent and the `feature.md` issue template now emit a `## Design Refs` section, and `create_ticket.py` ships a DoR-compliant `TEMPLATE_BODY` so manually-filed tickets start ready (#1489).

- [#1485](https://github.com/zealchaiwut/commander/issues/1485) Define canonical ticket-spec format and unified parser — 2026-06-22
- [#1487](https://github.com/zealchaiwut/commander/issues/1487) Surface ticket readiness on Planning board and gate Run Sprint — 2026-06-22
- [#1488](https://github.com/zealchaiwut/commander/issues/1488) Inject design context into coder prompt via Design Refs — 2026-06-22
- [#1489](https://github.com/zealchaiwut/commander/issues/1489) Emit Definition-of-Ready specs from BA agent — 2026-06-22

## Sprint 86

Continuation of the strangler-fig decomposition of the monolithic `services/sprint_manager/sprint_manager.py`. Cohesive helper clusters were lifted into dedicated submodules under `services/sprint_manager/`: event emission to `events.py` (#1275), model routing to `model_routing.py` (#1276), timekeeping helpers to `timekeeping.py` (#1277), pytest/lint and quality gates to `gates.py` (#1280, #1281), label-transition logic to `label_transitions.py` (#1282), worktree/env helpers to `worktree.py` (#1283), coder-dispatch logic to `dispatch.py` (#1285, part 1) with tester/doctor dispatch following (#1286, part 2), failure-handling logic to `failures.py` (#1279), pipeline dispatch to `pipeline.py` (#1289), sprint-summary generation to `summary.py` (#1287), and post-sprint agent orchestration to `post_sprint.py` (#1288). Within `sprint_manager.py` itself, the long `run_sprint` entry point was split into a `run_sprint_preflight()` preflight/branch-setup helper (#1290) and a `run_sprint_loop()` per-ticket loop helper (#1291). No behavior, endpoint, or schema changes — logic was relocated, not modified, leaving `sprint_manager.py` substantially slimmer.

- [#1275](https://github.com/zealchaiwut/commander/issues/1275) Extract sprint_manager event emission to events.py — 2026-06-21
- [#1276](https://github.com/zealchaiwut/commander/issues/1276) Extract sprint_manager model routing to dedicated module — 2026-06-21
- [#1277](https://github.com/zealchaiwut/commander/issues/1277) Extract timekeeping helpers to services/sprint_manager/timekeeping.py — 2026-06-21
- [#1279](https://github.com/zealchaiwut/commander/issues/1279) Extract sprint_manager failure handling to failures.py — 2026-06-22
- [#1280](https://github.com/zealchaiwut/commander/issues/1280) Extract pytest/lint gates to services/sprint_manager/gates.py — 2026-06-21
- [#1281](https://github.com/zealchaiwut/commander/issues/1281) Extract sprint_manager quality gates to gates.py — 2026-06-21
- [#1282](https://github.com/zealchaiwut/commander/issues/1282) Extract label transition logic to dedicated service module — 2026-06-21
- [#1283](https://github.com/zealchaiwut/commander/issues/1283) Extract worktree/env helpers to services/sprint_manager/worktree.py — 2026-06-21
- [#1285](https://github.com/zealchaiwut/commander/issues/1285) Extract coder dispatch logic to dispatch.py (Part 1) — 2026-06-21
- [#1286](https://github.com/zealchaiwut/commander/issues/1286) Extract tester/doctor dispatch to dispatch.py (part 2) — 2026-06-22
- [#1289](https://github.com/zealchaiwut/commander/issues/1289) Extract sprint_manager pipeline dispatch to services module — 2026-06-22
- [#1287](https://github.com/zealchaiwut/commander/issues/1287) Extract sprint summary generation to dedicated module — 2026-06-21
- [#1288](https://github.com/zealchaiwut/commander/issues/1288) Extract post-sprint agents to post_sprint.py — 2026-06-21
- [#1290](https://github.com/zealchaiwut/commander/issues/1290) Extract run_sprint preflight/branch-setup into helper — 2026-06-22
- [#1291](https://github.com/zealchaiwut/commander/issues/1291) Extract per-ticket loop from run_sprint into helper — 2026-06-22
## Sprint 94.4

Cross-project sprint isolation. Sprint `label` values are unique only *within* a `project`, so two projects (e.g. `commander` and `perf-coach`) can each own a `sprint-66`; this sprint enforces that `(label, project)` composite-key invariant end to end. All sprint-children reads now scope to project — `get_sprint_children(parent_label, project)` and `children_of(parent_label, …, project)` filter by project (label-only paths kept as a warning-logged fallback), and the outcome, bulk-complete, and merge-chain callers in `startup.py` pass project through, eliminating cross-project lineage leakage. A new `_backfill_sprint_project()` migration (run in `_create_sprint_lifecycle_tables`, also exposed as `scripts/backfill_sprint_project.py`) fills empty `sprints.project` / `sprint_history.project` rows by resolving each label through `agent_runs` → disk plan/state files → warn-and-skip; it is idempotent and only touches empty rows. The historical sprint-66 collision (perf-coach's row overwrote commander's via `ON CONFLICT(label)`, orphaning the `66.x` children) is repaired by `scripts/repair_sprint_collisions.py --apply` and guarded by `tests/test_sprint_collision_regression.py`, with the full invariant documented in `docs/architecture/sprint-lifecycle.md`.

- [#1460](https://github.com/zealchaiwut/commander/issues/1460) Backfill sprints.project for legacy unattributed rows — 2026-06-21
## Sprint 94.2

Sprint label collision hardening. The `sprints` table keys rows by `label` alone, so two projects that reuse a sprint label (e.g. `sprint-66`) compete for the same row — one project's row clobbers the other via `ON CONFLICT(label) DO UPDATE`. This sprint adds the audit, fix, and read-path scoping to stop that. A read-only auditor (`scripts/audit_sprint_collisions.py`) cross-references `sprints`, `sprint_history`, `agent_runs`, and per-clone `plan.json`/`state.json` to surface every label claimed by more than one project, naming the survivor and each losing project; it prints a markdown table and writes a `.commander/runtime/sprint-collisions.json` manifest, surfaced over `GET /api/debug/sprint-collisions`. All cross-project leakage in the sprint read path is closed by threading a `project` argument through `db.get_sprint_children` and `startup.children_of` (and their callers) so child-sprint lookups scope to one project instead of matching `parent_label` globally; the unscoped path now logs a warning. A surgical repair (`scripts/repair_sprint_collisions.py`, `--dry-run`/`--apply`) restores commander's clobbered `sprint-66` base row from `plan.json → state.json → agent_runs` without touching perf-coach's row, and asserts no stale running row survives.

- [#1461](https://github.com/zealchaiwut/commander/issues/1461) Audit and report sprint label collisions before key migration — 2026-06-21
## Sprint 94.1

Sprint label collisions across projects are eliminated by migrating the SQLite `sprints` table from a single-column `label` primary key to a **composite `(label, project)` primary key**. A new one-shot migration (`_migrate_sprints_to_composite_pk`), gated on a `_sprint_schema_migrations` version table, rebuilds the table and deduplicates existing rows on `(label, project)` (highest-rowid wins). All sprint DB writes — `transition_sprint_state`, `_set_sprint_terminal`, `update_sprint_run_counts`, `ingest_sprint_run_artifact`, and the `record_sprint_*` helpers — now scope on `(label, project)` (upserting `ON CONFLICT(label, project)`), and all sprint-children reads (`get_sprint_children`, `children_of`, plus the reconcile/finish/merge-chain callers) take an explicit `project` and warn on the label-only fallback, so a running sprint in one project can no longer leak into another's lifecycle. A repair script (`scripts/repair_sprint_collisions.py`) audits and fixes pre-migration `sprint-66` collisions and guards the composite-key invariant.

- [#1462](https://github.com/zealchaiwut/commander/issues/1462) Migrate sprints table to composite (label, project) primary key — 2026-06-21
- [#1463](https://github.com/zealchaiwut/commander/issues/1463) Scope all sprint DB writes to (label, project) — 2026-06-21
- [#1464](https://github.com/zealchaiwut/commander/issues/1464) Scope all sprint reads to project, eliminate cross-project leakage — 2026-06-21
- [#1465](https://github.com/zealchaiwut/commander/issues/1465) Repair sprint-66 collisions and guard composite-key invariant — 2026-06-21

## Sprint 93

Sprint-planning board gains DAG-aware ordering tools. An **Apply DAG Order** button on each planned/draft sprint card (shown only when a DAG preview with levels and no cycles is cached) reorders tickets to follow the dispatch DAG — lower topological levels first, within-level order preserved, unlevelled tickets appended last. The reorder is computed by a new read-only `GET /api/sprints/{sprint_label}/dag-order-preview` endpoint (`compute_dag_order`) that returns the proposed order, a human-readable diff with dependency-edge hints, an `is_noop` flag, and the `partial` flag from `preview-dag`; it returns HTTP 409 on circular dependencies and adds zero GitHub calls. The preview-dag rail surfaces inline fix chips for file conflicts and dependency cycles, and the board now warns when a manual ticket order violates DAG levels (a downstream ticket placed before its upstream dependency) with a one-click auto-fix (`compute_order_violations` / `compute_fix_order_slot`) that slots the offending ticket to the earliest valid position after all its dependencies. Partial preview-dag runs (some tickets unestimated) now display a pre-run checklist on the mini-rail.

- [#1420](https://github.com/zealchaiwut/commander/issues/1420) Add Apply DAG Order button to sprint planning board — 2026-06-20
- [#1421](https://github.com/zealchaiwut/commander/issues/1421) Add inline conflict/cycle fix chips to preview-dag — 2026-06-20
- [#1422](https://github.com/zealchaiwut/commander/issues/1422) Warn and auto-fix manual order violating DAG levels — 2026-06-20
- [#1425](https://github.com/zealchaiwut/commander/issues/1425) Show pre-run checklist on partial preview-dag runs — 2026-06-20

## Sprint 92

Concurrent multi-coder dispatch. The sprint runner can now drive more than one coder at a time, each in its own isolated git worktree, instead of the prior one-coder-at-a-time serial loop. A warm worktree pool (`services/sprint_manager/worktree_pool.py`) pre-creates K isolated worktrees — each with its own fresh virtualenv — under `.commander/runtime/worktree-pool/` at sprint start, reconciling any orphans left by a prior crash and tearing the pool down at the end; pool size comes from `max_coder_slots` (default 2, cap 4). A new conflict-aware concurrent scheduler (`concurrent_scheduler.py`) fans tickets across role-flexible slots: each slot can run a code task or a test task, preferring code to keep the pipeline fed and falling back to tests when no eligible code task is available, with file-overlap checks gating both and a tester rejection re-queued to the front of the coder queue. When `max_coder_slots <= 1` it delegates to the existing pipeline path for zero behavioural divergence. Concurrent merges are serialized through a develop-merge guard, and on a merge conflict `finish_feature` now attempts a single automated rebase before falling back to manual. The DAG builder honours explicit ticket dependencies (issue #1404). On the dashboard, the live snapshot now reports lane capacity (`max_coder_slots` / `max_tester_slots`) from run start and lists every active coder as its own entry in `active_agents`, and the project page renders a multi-lane running view for concurrent coder runs. Finally, `finish_feature` records estimator file-prediction accuracy (precision/recall of `files_likely_affected` vs the files the merge actually touched) per ticket under `.commander/estimates/accuracy/`, and `preview-dag` surfaces an `accuracy_warning` when recent predictions are unreliable.

- [#1404](https://github.com/zealchaiwut/commander/issues/1404) Merge explicit ticket dependencies into sprint dispatch DAG — 2026-06-20
- [#1411](https://github.com/zealchaiwut/commander/issues/1411) Add warm git worktree pool for concurrent coder dispatch — 2026-06-20
- [#1412](https://github.com/zealchaiwut/commander/issues/1412) Add conflict-aware concurrent scheduler to sprint runner — 2026-06-20
- [#1413](https://github.com/zealchaiwut/commander/issues/1413) Make worker pool slots role-flexible across code and test tasks — 2026-06-20
- [#1414](https://github.com/zealchaiwut/commander/issues/1414) Serialize concurrent merges with automated rebase on conflict — 2026-06-20
- [#1415](https://github.com/zealchaiwut/commander/issues/1415) Populate slot capacity in live sprint snapshot payload — 2026-06-20
- [#1416](https://github.com/zealchaiwut/commander/issues/1416) Add multi-lane live view for concurrent runs — 2026-06-20
- [#1417](https://github.com/zealchaiwut/commander/issues/1417) Track estimator file-prediction accuracy after ticket merges — 2026-06-20

## Sprint 91

Coder-dispatch efficiency improvements driven by the estimator. Tickets whose estimate is docs/config-only — flagged `docs-only` or where every likely-affected path is a doc/config file (`.md`/`.yaml`/`.json` or under `docs/`) with no code paths — now route to Haiku regardless of size, except XL is never rerouted; the routing reason (`docs-only:flag` / `docs-only:paths`) surfaces as a tooltip on the coder badge in the live running pane. The estimator's `files_touched`/`files_likely_affected` paths are now injected as a "Start here" block at the top of the coder dispatch prompt so the agent skips a broad repo search. The feature template and BA ticket generation gained an optional `## Files to touch` section; the estimator parses it and unions those explicit paths (first) with its own inferred ones into `files_likely_affected`. And pipeline-mode dispatch now mirrors the serial loop's consecutive-identical-failure early abort via a new `StageResult.EXHAUST` — a ticket that fails the same gate twice in a row is finalized as needs-rework immediately instead of burning all three fix rounds.

- [#1401](https://github.com/zealchaiwut/commander/issues/1401) Port identical-failure early abort to pipeline dispatch — 2026-06-20
- [#1402](https://github.com/zealchaiwut/commander/issues/1402) Inject estimator target paths into coder dispatch prompts — 2026-06-20
- [#1403](https://github.com/zealchaiwut/commander/issues/1403) Route docs-only and config-only tickets to Haiku — 2026-06-20
- [#1405](https://github.com/zealchaiwut/commander/issues/1405) Add 'Files to touch' section to feature template and estimator — 2026-06-20

## Sprint 85.5

Continuation of the backend decomposition (strangler-fig): mis-sizing flag routes were lifted out of `apps/dashboard/server.py` into `apps/dashboard/routers/mis_sizing.py` (delegating to `services/sprint_manager/mis_sizing.py`). Analytics and metrics routes from #1252 are covered by the #1267 estimates/calibration/metrics router split. No behavior, endpoint, or schema changes — routes and logic were relocated, not modified.

- [#1253](https://github.com/zealchaiwut/commander/issues/1253) Extract mis-sizing routes from server.py to dedicated router — 2026-06-19

## Sprint 85

Backend decomposition sprint (strangler-fig): the dashboard's monolithic `apps/dashboard/server.py` was slimmed by extracting route clusters into dedicated `apps/dashboard/routers/*.py` modules (system/health, page-serving, project branches, preflight & DAG, sprint live/log, sprint CRUD, sprint summary & home, system/misc, sprint finish preview/write, sprint run read/write, and bulk job reads/writes/per-job actions), with `server.py` finally reduced to a thin app factory whose remaining helpers live in `apps/dashboard/startup.py`; `services/sprint_manager/sprint_manager.py` was split into focused submodules (`state.py` data classes, `config.py` config loading, `paths.py` path helpers, `alerts.py` alert channels). No behavior, endpoint, or schema changes — routes and logic were relocated, not modified.

- [#1247](https://github.com/zealchaiwut/commander/issues/1247) Extract system/health routes from server.py to routers/system.py — 2026-06-17
- [#1248](https://github.com/zealchaiwut/commander/issues/1248) Extract page-serving handlers from server.py to routers/pages.py — 2026-06-17
- [#1250](https://github.com/zealchaiwut/commander/issues/1250) Extract project branch routes to dedicated router — 2026-06-17
- [#1254](https://github.com/zealchaiwut/commander/issues/1254) Extract preflight & DAG routes to sprint_preflight router — 2026-06-17
- [#1255](https://github.com/zealchaiwut/commander/issues/1255) Extract sprint live/log routes to sprint_live router — 2026-06-17
- [#1257](https://github.com/zealchaiwut/commander/issues/1257) Extract sprint CRUD routes to routers/sprint_crud.py — 2026-06-17
- [#1258](https://github.com/zealchaiwut/commander/issues/1258) Extract sprint summary & home routes to dedicated router — 2026-06-17
- [#1259](https://github.com/zealchaiwut/commander/issues/1259) Extract system/misc routes from server.py to routers/system_misc.py — 2026-06-17
- [#1260](https://github.com/zealchaiwut/commander/issues/1260) Extract sprint finish preview routes to routers/sprint_finish.py — 2026-06-17
- [#1261](https://github.com/zealchaiwut/commander/issues/1261) Extract sprint finish write routes to routers/sprint_finish.py — 2026-06-17
- [#1262](https://github.com/zealchaiwut/commander/issues/1262) Extract sprint run read/preview routes to dedicated router — 2026-06-17
- [#1263](https://github.com/zealchaiwut/commander/issues/1263) Extract sprint run routes from server.py to service module — 2026-06-17
- [#1264](https://github.com/zealchaiwut/commander/issues/1264) Extract bulk job read routes to routers/bulk_tickets.py — 2026-06-17
- [#1265](https://github.com/zealchaiwut/commander/issues/1265) Extract bulk-ticket draft/create routes to routers/bulk_tickets.py — 2026-06-17
- [#1266](https://github.com/zealchaiwut/commander/issues/1266) Extract bulk-ticket per-job action routes to routers/bulk_tickets.py — 2026-06-17
- [#1267](https://github.com/zealchaiwut/commander/issues/1267) Slim server.py to a thin app factory; extract remaining routes/helpers to routers/ and startup.py — 2026-06-17
- [#1268](https://github.com/zealchaiwut/commander/issues/1268) Extract sprint_manager data classes to state.py — 2026-06-17
- [#1269](https://github.com/zealchaiwut/commander/issues/1269) Extract sprint_manager config loading to config.py — 2026-06-17
- [#1270](https://github.com/zealchaiwut/commander/issues/1270) Extract sprint_manager path helpers to paths.py — 2026-06-17
- [#1271](https://github.com/zealchaiwut/commander/issues/1271) Extract sprint_manager alert channels to alerts.py — 2026-06-17

## Sprint 90

Follow-up cleanups from sprint-89 code review, all internal refactors with no UI changes. Sprint summary count math is now consistent across paths: `_compute_summary_counts` gives column-status unconditional priority (a pending/in-progress/sit issue is never settled-done even with `agent_status='completed'`), matching the canonical `_settled_done_from_columns` formula, and the reconcile path now re-derives `summary_uat_count` from issue column status instead of preserving the stored value. Calibration size resolution gained a final fallback to the local SQLite `issues` mirror's `size-*` label (no GitHub call) when neither the estimate JSON nor the state file has a size. The duplicate `GET /api/sprints/{label}/state` timing handler — previously unreachable, shadowed by the plan.json handler on the same path — moved to `GET /api/sprints/{label}/state-timing`. `maintenance_service` no longer imports the `server` FastAPI monolith for calibration helpers — it imports `calibration_cache_service` directly, breaking the router→monolith cycle. Plus a stray test removal, duplicate §1.7 doc-test consolidation, a no-op self-assignment removal, and import/include-router tidy in `server.py`.

- [#1295](https://github.com/zealchaiwut/commander/issues/1295) Re-derive summary_uat_count in reconcile path — 2026-06-17
- [#1296](https://github.com/zealchaiwut/commander/issues/1296) Narrow settled-done equivalence between materialize and canonical formula — 2026-06-17
- [#1297](https://github.com/zealchaiwut/commander/issues/1297) Clarify ambiguous and/or precedence in _compute_summary_counts — 2026-06-17
- [#1301](https://github.com/zealchaiwut/commander/issues/1301) Move shadowed sprint-state timing route to /state-timing — 2026-06-17
- [#1337](https://github.com/zealchaiwut/commander/issues/1337) Remove stray test_bulk_routes_extraction__1265.py — 2026-06-17
- [#1338](https://github.com/zealchaiwut/commander/issues/1338) Consolidate duplicate §1.7 doc tests for #1298 — 2026-06-18
- [#1340](https://github.com/zealchaiwut/commander/issues/1340) maintenance_service imports calibration_cache_service directly, not the server monolith — 2026-06-18
- [#1341](https://github.com/zealchaiwut/commander/issues/1341) Calibration size label fallback reads SQLite issues mirror — 2026-06-17
- [#1342](https://github.com/zealchaiwut/commander/issues/1342) Remove no-op self-assignment _CALIBRATION_SIZES = _CALIBRATION_SIZES — 2026-06-17
- [#1343](https://github.com/zealchaiwut/commander/issues/1343) Tidy maintenance_router import and include_router placement in server.py — 2026-06-17

## Sprint 89

Calibration accuracy hardening. Estimate JSON is now written to one canonical path (`<project-root>/.commander/estimates/issue-N.json`) by every writer, and calibration resolves each completed ticket's size through a three-tier fallback (canonical JSON → sprint-state estimate → `size-*` label) so tickets estimated only at creation still appear in history. Added a calibration-cache rebuild — `POST /api/maintenance/calibration/rebuild?project=<slug>` and CLI `scripts/rebuild_calibration_cache.py` — that clears and rescans all `sprint-*-state.json` files (live + archive) to surface full sprint history. The cache now auto-refreshes when a sprint finishes. Preflight auto-fix writes the canonical JSON alongside the `size-*` label (warns if the subprocess exits 0 but the JSON is missing). New maintenance helper `scripts/collect_stray_estimates.py` copies stray clone-local estimate JSONs to the canonical project-root location. See `docs/features/estimation-lifecycle.md`.

- [#1331](https://github.com/zealchaiwut/commander/issues/1331) Fix estimate JSON write path and calibration size resolution — 2026-06-17
- [#1332](https://github.com/zealchaiwut/commander/issues/1332) Rebuild calibration cache to surface full sprint history — 2026-06-17
- [#1333](https://github.com/zealchaiwut/commander/issues/1333) Auto-refresh calibration cache on sprint finish — 2026-06-17
- [#1334](https://github.com/zealchaiwut/commander/issues/1334) Fix calibration hygiene: mis-sizing, preflight JSON, docs — 2026-06-17

## Sprint 88

Follow-up cleanups from code review. The Sprint History/summary architecture doc (§1.7) now states the actual contract — no SQLite row means a 404/`no_data`, with ingestion running at end-of-run only (the on-demand HTTP ingest path was removed in #1161) — instead of describing the removed lazy-ingest behavior. The bulk-job routes drop redundant disk-reload blocks that duplicated logic already inside `_get_bulk_job`. And `setup_machine.sh`'s `_get_env_val` now skips commented-out `.env` lines so a commented key no longer suppresses its setup prompt; only an uncommented `KEY=value` counts as the effective value.

- [#1298](https://github.com/zealchaiwut/commander/issues/1298) Resolve disk-read contradiction in source-of-truth doc 1.7 — 2026-06-17
- [#1302](https://github.com/zealchaiwut/commander/issues/1302) Remove redundant disk-reload after _get_bulk_job in bulk_tickets routes — 2026-06-17
- [#1312](https://github.com/zealchaiwut/commander/issues/1312) setup_machine.sh _get_env_val skips commented-out values so they no longer count as already-set — 2026-06-17

## Sprint 87

Hardening pass on `setup_machine.sh` host provisioning: it now prompts for every secret key declared in `.env.example` (any key ending in `_TOKEN`, `_KEY`, `_SECRET`, or `_PASSWORD`) instead of only `GH_TOKEN`, and re-runs against an existing `.env` to fill in any still-unset (empty or `<placeholder>`) secret without overwriting keys already set to real values. `--restore-db` now passes `--force` to `backup restore-db`, so re-running it on a machine that already has `commander.db` succeeds instead of erroring. Added an `ANTHROPIC_API_KEY` entry to `.env.example`.

- [#819](https://github.com/zealchaiwut/commander/issues/819) setup_machine.sh prompts for all secret keys in .env.example, not just GH_TOKEN — 2026-06-17
- [#822](https://github.com/zealchaiwut/commander/issues/822) setup_machine.sh --restore-db passes --force so re-running on an existing commander.db succeeds — 2026-06-17

## Sprint 84

Mobile-responsive pass over the project dashboard (`project.html`): added scoped `@media` breakpoints so the Sprint-Mgmt board, running pane, logs, Bulk Create, and Sprint History panes stack, wrap, and gain 44px tap targets on narrow viewports without changing desktop layout.

- [#1178](https://github.com/zealchaiwut/commander/issues/1178) Stack sprint-card header vertically on mobile phones — 2026-06-16
- [#1179](https://github.com/zealchaiwut/commander/issues/1179) Wrap backlog filter pills on narrow screens — 2026-06-16
- [#1180](https://github.com/zealchaiwut/commander/issues/1180) Add 44px tap targets to backlog header controls — 2026-06-16
- [#1181](https://github.com/zealchaiwut/commander/issues/1181) Truncate sprint mini-rail badges on mobile — 2026-06-16
- [#1182](https://github.com/zealchaiwut/commander/issues/1182) Log filter chips: two-column layout on mobile — 2026-06-16
- [#1183](https://github.com/zealchaiwut/commander/issues/1183) Stack logs search and view toggle on narrow screens — 2026-06-16
- [#1184](https://github.com/zealchaiwut/commander/issues/1184) Truncate run IDs and fail titles on mobile — 2026-06-16
- [#1185](https://github.com/zealchaiwut/commander/issues/1185) Mobile: horizontal-scroll raw log stream on small screens — 2026-06-16
- [#1186](https://github.com/zealchaiwut/commander/issues/1186) Stack Bulk Create settings bar fields on mobile — 2026-06-16
- [#1187](https://github.com/zealchaiwut/commander/issues/1187) Reflow Bulk Create draft-card header on mobile — 2026-06-16
- [#1188](https://github.com/zealchaiwut/commander/issues/1188) Wrap estimate badges on narrow screens in Bulk Create — 2026-06-16
- [#1189](https://github.com/zealchaiwut/commander/issues/1189) Fix Bulk Create textarea and prompt-row mobile overflow — 2026-06-16
- [#1190](https://github.com/zealchaiwut/commander/issues/1190) Fix metrics strip overflow on mobile viewports — 2026-06-16
- [#1191](https://github.com/zealchaiwut/commander/issues/1191) Wrap rail nodes and truncate titles on mobile — 2026-06-16
- [#1192](https://github.com/zealchaiwut/commander/issues/1192) Stack lane capacity dots on mobile screens — 2026-06-16
- [#1193](https://github.com/zealchaiwut/commander/issues/1193) Make inspector a bottom sheet on mobile — 2026-06-16
- [#1194](https://github.com/zealchaiwut/commander/issues/1194) Stack Sprint History card header on mobile — 2026-06-16
- [#1195](https://github.com/zealchaiwut/commander/issues/1195) Wrap Sprint History metrics row on narrow screens — 2026-06-16
- [#1196](https://github.com/zealchaiwut/commander/issues/1196) Wrap Sprint History action buttons for mobile touch targets — 2026-06-16
- [#1197](https://github.com/zealchaiwut/commander/issues/1197) Fix Sprint History mobile reflow and Gantt overflow — 2026-06-16

## Sprint 83.1

Source-of-truth hardening for the sprint read path: history and summary panes now read sprint metrics from SQLite only — disk-read fallbacks removed and a lazy-ingest path fills the DB on first access. Run finish materializes denormalized count columns, the reconcile job repairs stale counts, and the store-level contract is now documented in the architecture docs.

- [#1160](https://github.com/zealchaiwut/commander/issues/1160) Lazy-ingest Sprint History read path to eliminate disk/DB fork — 2026-06-17
- [#1161](https://github.com/zealchaiwut/commander/issues/1161) Remove disk-read fallbacks from sprint-summary readers — 2026-06-17
- [#1162](https://github.com/zealchaiwut/commander/issues/1162) Extend reconcile job to fix denormalized sprint counts — 2026-06-17
- [#1163](https://github.com/zealchaiwut/commander/issues/1163) Materialize sprint_summary row on run finish — 2026-06-17
- [#1164](https://github.com/zealchaiwut/commander/issues/1164) Document source-of-truth contract in architecture docs — 2026-06-17

## Sprint 82

Dashboard UI redesign sprint: four tabs — Backlog/Tickets, Logs, Analytics, and Roadmap/Milestones — were rebuilt on token-based styling, audited against the impeccable design rules (WCAG AA contrast, spacing, color discipline), and polished for accessibility (accessible filter chips, skeleton loading states, motion-safe transitions). No backend or schema changes.

- [#1059](https://github.com/zealchaiwut/commander/issues/1059) Redesign Backlog/Tickets tab with token styling — 2026-06-16
- [#1060](https://github.com/zealchaiwut/commander/issues/1060) Audit and fix Backlog tab impeccable violations — 2026-06-16
- [#1061](https://github.com/zealchaiwut/commander/issues/1061) Polish Backlog/Tickets tab UX and accessibility — 2026-06-16
- [#1062](https://github.com/zealchaiwut/commander/issues/1062) Redesign Logs tab UI with token-based styling — 2026-06-16
- [#1065](https://github.com/zealchaiwut/commander/issues/1065) Polish Logs tab UI with accessible filter chips — 2026-06-16
- [#1066](https://github.com/zealchaiwut/commander/issues/1066) Redesign Analytics page with token-based sub-tab layout — 2026-06-16
- [#1067](https://github.com/zealchaiwut/commander/issues/1067) Audit and fix Analytics page against impeccable rules — 2026-06-16
- [#1068](https://github.com/zealchaiwut/commander/issues/1068) Polish Analytics page UI and accessibility — 2026-06-16
- [#1069](https://github.com/zealchaiwut/commander/issues/1069) Redesign Roadmap/Milestones tab with token styling — 2026-06-16
- [#1070](https://github.com/zealchaiwut/commander/issues/1070) Audit and fix Roadmap tab UI against impeccable rules — 2026-06-16

## Sprint 80

Front-end design-system pass: a shared design-tokens stylesheet linked to every page, plus redesign/polish/audit passes over the Home page, project shell navigation, and the sprint board for token consistency, scannability, and accessibility.

- [#1045](https://github.com/zealchaiwut/commander/issues/1045) Add design tokens CSS and link to all pages — 2026-06-16
- [#1047](https://github.com/zealchaiwut/commander/issues/1047) Audit and fix Home page against impeccable rules — 2026-06-16
- [#1048](https://github.com/zealchaiwut/commander/issues/1048) Polish Home page: hover, motion, a11y, empty/loading states — 2026-06-16
- [#1049](https://github.com/zealchaiwut/commander/issues/1049) Redesign project shell and global navigation chrome — 2026-06-16
- [#1053](https://github.com/zealchaiwut/commander/issues/1053) Audit and fix project shell nav anti-patterns — 2026-06-16
- [#1054](https://github.com/zealchaiwut/commander/issues/1054) Polish project shell nav: a11y, transitions, sticky header — 2026-06-16
- [#1055](https://github.com/zealchaiwut/commander/issues/1055) Redesign sprint board for scannability and token consistency — 2026-06-16
- [#1056](https://github.com/zealchaiwut/commander/issues/1056) Audit and fix sprint board impeccable violations — 2026-06-16

## Sprint 79

Running-pane redesign: the level-rail and node inspector were replaced by a collapsible Orchestrator log panel and a single All Issues panel grouped by dispatch level, and a four-segment progress gauge (done · retrying · running · queued) was added to the pane header.

- [#1106](https://github.com/zealchaiwut/commander/issues/1106) Consolidate running pane into single group (Orchestrator + All Issues panels) — 2026-06-15
- [#1107](https://github.com/zealchaiwut/commander/issues/1107) Add segmented progress gauge to running pane header — 2026-06-15

## Sprint 78

Per-issue Gantt timeline in the running-sprint pane, backed by a DB-sourced timeline endpoint, plus a chip-only lane assignment map.

- [#1108](https://github.com/zealchaiwut/commander/issues/1108) Reduce running-pane lanes to chip-only assignment map — 2026-06-15
- [#1146](https://github.com/zealchaiwut/commander/issues/1146) Build timeline data endpoint for running sprint pane — 2026-06-15
- [#1147](https://github.com/zealchaiwut/commander/issues/1147) Render per-issue Gantt timeline in running pane — 2026-06-15

## Sprint 77

- [#1041](https://github.com/zealchaiwut/commander/issues/1041) Redesign sprint history cards: hierarchy, dedup, color discipline — 2026-06-15
- [#1042](https://github.com/zealchaiwut/commander/issues/1042) Sprint cards: one action, status line, budget color — 2026-06-15
- [#1043](https://github.com/zealchaiwut/commander/issues/1043) Board: collapse ancestor sprints with merge-state marks — 2026-06-15
- [#1044](https://github.com/zealchaiwut/commander/issues/1044) Board: planning layout with draft sprint and focus guide — 2026-06-15

## Sprint 75

- [#876](https://github.com/zealchaiwut/commander/issues/876) Format recent-activity timestamp to short local `HH:MM` time on home brief — 2026-06-15

## Sprint 74.2

DB-only sprint lifecycle: a single guarded write path and a single read accessor, eliminating plan.json / label / PID drift.

- [#1089](https://github.com/zealchaiwut/commander/issues/1089) Gate startup sweep before flipping live running sprints — 2026-06-15
- [#1090](https://github.com/zealchaiwut/commander/issues/1090) Remove reverse-heal from `_is_sprint_running` — 2026-06-15
- [#1091](https://github.com/zealchaiwut/commander/issues/1091) Add canonical sprint lifecycle read accessor (`sprint_state.current`) — 2026-06-15
- [#1092](https://github.com/zealchaiwut/commander/issues/1092) Migrate History pane to DB-only lifecycle accessor — 2026-06-15
- [#1093](https://github.com/zealchaiwut/commander/issues/1093) Migrate `_derive_outcome_lifecycle` to DB-only state — 2026-06-15
- [#1096](https://github.com/zealchaiwut/commander/issues/1096) Make plan.json read-only from GET endpoints — 2026-06-15
- [#1097](https://github.com/zealchaiwut/commander/issues/1097) Consolidate child-sprint resolvers into single DB-backed function — 2026-06-15
- [#1098](https://github.com/zealchaiwut/commander/issues/1098) Add cross-store invariant test and green suite — 2026-06-15

## Hotfix — board-history-running-ux (2026-06-14)

Operator-driven UX hotfix to the Sprint-Mgmt Board / Running / History panes
and analytics. No GitHub ticket — shipped directly to `hotfix/board-history-running-ux`.

- **#1** History rows show ticket title (not just number) and are clickable, opening the GitHub issue in a new tab — matches the Board affordance. (`sprint_history_service._normalize_issue` passes title through; client falls back to the board's per-sprint cache; rows get `.iss-row-link`.)
- **#2** Removed the redundant "Sprint X running" label from the project header (`proj-header-pill`). The sprint-nav status pill + sub-nav running dot already signal it.
- **#3** Calibration / est-vs-actual (`_compute_calibration` in `server.py`) now counts lifecycle done-equivalent statuses (`done`, `uat`, `merged`, `passed`) so newer sprints no longer blank the per-size record / est-vs-actual plot.
- **#4** Batch-select bar anchors directly above the card of the sprint whose tickets are selected (single-sprint); cross-sprint falls back to the top. (`_smgmtPositionSelectionBar` in `drag-drop.js`.)
- **#5** Running sprints default to collapsed on the Board (live detail is in the Running pane); card header gets an "Open in Running" deep-link. Collapse pref is now tri-state (`'1'` collapsed / `'0'` explicitly expanded / absent = default).
- **#6** Board/Running/History sub-view is persisted to `sessionStorage` per project; page refresh / auto-refresh returns the user to the sub-view they were on instead of snapping to Board.
- **#7** Finished sprints (`completed` / `ready_to_merge` / `partial_finished`) get a "History" deep-link in the card header that opens the History sub-view and focuses that sprint.
- **#8** Removed the sprint-level "Fix rounds X/2" tile from the Running metrics strip (it summed per-issue rounds and could read e.g. 9/2). The strip now shows "Retrying: N tickets" only when any ticket is currently in a fix round.

## Sprint 73

- [#931](https://github.com/zealchaiwut/commander/issues/931) Bulk Create: Use Shared Progress Component (Tier 1) — 2026-06-14
- [#927](https://github.com/zealchaiwut/commander/issues/927) Running pane: two-queue coder/tester lane view — 2026-06-14
- [#930](https://github.com/zealchaiwut/commander/issues/930) Batch reestimate: show shared progress bar component — 2026-06-14
- [#933](https://github.com/zealchaiwut/commander/issues/933) Show pre-flight checks as live stepper checklist — 2026-06-14

## Sprint 71

- [#916](https://github.com/zealchaiwut/commander/issues/916) Port commander invariants into .clinerules — 2026-06-14
- [#917](https://github.com/zealchaiwut/commander/issues/917) Add Cline headless backend to coder dispatch — 2026-06-14
- [#918](https://github.com/zealchaiwut/commander/issues/918) Route follow-up coder dispatches to Cline on sprint opt-in — 2026-06-14
- [#920](https://github.com/zealchaiwut/commander/issues/920) Tag agent runs with backend; escalate Cline gate failures — 2026-06-14

## Sprint 68

- [#894](https://github.com/zealchaiwut/commander/issues/894) Remove unreachable exhausted-retries raise in _run_step — 2026-06-12

## Sprint 65

- [#852](https://github.com/zealchaiwut/commander/issues/852) Build launchd plist PATH from real tool locations at install time — 2026-06-11
- [#853](https://github.com/zealchaiwut/commander/issues/853) Wire headless auth tokens into launchd service at install — 2026-06-11
- [#855](https://github.com/zealchaiwut/commander/issues/855) Add machine onboarding runbook to docs — 2026-06-11
- [#856](https://github.com/zealchaiwut/commander/issues/856) Add post-sprint reconciliation check for loose ends — 2026-06-11
- [#857](https://github.com/zealchaiwut/commander/issues/857) Verify New Sprint creation and surface failures loudly — 2026-06-11
- [#858](https://github.com/zealchaiwut/commander/issues/858) Show per-agent timing and error detail on Logs tab — 2026-06-11
- [#859](https://github.com/zealchaiwut/commander/issues/859) Wire Analytics Metrics, Status, and Trends to real data — 2026-06-11

## Sprint 64

- [#839](https://github.com/zealchaiwut/commander/issues/839) Add brief assembly API for project and home roll-up — 2026-06-11
- [#840](https://github.com/zealchaiwut/commander/issues/840) Generate and cache LLM brief summary with fallback — 2026-06-11
- [#841](https://github.com/zealchaiwut/commander/issues/841) Store and serve daily brief as persistent artifact — 2026-06-11
- [#842](https://github.com/zealchaiwut/commander/issues/842) Build home page: daily brief with project blocks — 2026-06-11
- [#843](https://github.com/zealchaiwut/commander/issues/843) Add per-project to-do list table and API — 2026-06-11
- [#844](https://github.com/zealchaiwut/commander/issues/844) Build per-project to-do list panel UI — 2026-06-11

## Sprint 62

- [#773](https://github.com/zealchaiwut/commander/issues/773) Pre-fill .env setting field with current value — 2026-06-11
- [#774](https://github.com/zealchaiwut/commander/issues/774) Project Settings: add icon & color pickers — 2026-06-11
- [#811](https://github.com/zealchaiwut/commander/issues/811) Strip stale status labels when sprint is re-run — 2026-06-11
- [#826](https://github.com/zealchaiwut/commander/issues/826) Resolve tool paths dynamically in install_launchd.sh — 2026-06-11
- [#827](https://github.com/zealchaiwut/commander/issues/827) Wire headless auth tokens into launchd service at install — 2026-06-11
- [#828](https://github.com/zealchaiwut/commander/issues/828) Add machine-doctor command for pre-sprint host validation — 2026-06-11
- [#829](https://github.com/zealchaiwut/commander/issues/829) Add machine onboarding runbook to docs — 2026-06-11

## Sprint 61

- [#798](https://github.com/zealchaiwut/commander/issues/798) Rename Sprint tab and introduce Board/Running/History sub-nav — 2026-06-11
- [#800](https://github.com/zealchaiwut/commander/issues/800) Add filtered multi-select backlog panel with what-if delta — 2026-06-11
- [#801](https://github.com/zealchaiwut/commander/issues/801) Add sprint capacity budget bar to sprint pane — 2026-06-11
- [#802](https://github.com/zealchaiwut/commander/issues/802) Render level-rail node board in Running view — 2026-06-11
- [#803](https://github.com/zealchaiwut/commander/issues/803) Add running metrics strip above the rail — 2026-06-11
- [#804](https://github.com/zealchaiwut/commander/issues/804) Add node inspector panel with per-issue log tabs — 2026-06-11
- [#805](https://github.com/zealchaiwut/commander/issues/805) Add sprint history endpoint and persist deleted-sprint records — 2026-06-11
- [#806](https://github.com/zealchaiwut/commander/issues/806) History ledger: issue lists, verb rules, locking, links — 2026-06-11
- [#807](https://github.com/zealchaiwut/commander/issues/807) Add history folding and fold-size setting — 2026-06-11
- [#808](https://github.com/zealchaiwut/commander/issues/808) Add stale-branch scan and cleanup to History — 2026-06-11
- [#809](https://github.com/zealchaiwut/commander/issues/809) Add preview DAG endpoint and mini-rail UI — 2026-06-11
- [#810](https://github.com/zealchaiwut/commander/issues/810) Add run-stats block and gantt timeline to History cards — 2026-06-11

## Sprint 60

- [#793](https://github.com/zealchaiwut/commander/issues/793) Document boundary map: routers, services, repos — 2026-06-11
- [#794](https://github.com/zealchaiwut/commander/issues/794) Extract system/health and activity/logs routers from server.py — 2026-06-11
- [#795](https://github.com/zealchaiwut/commander/issues/795) Extract sprints and tickets routers from server.py — 2026-06-11
- [#796](https://github.com/zealchaiwut/commander/issues/796) Add esbuild pipeline and extract log-panel module — 2026-06-11

## Sprint 59

- [#783](https://github.com/zealchaiwut/commander/issues/783) Add forensic run browser to dashboard — 2026-06-11
- [#784](https://github.com/zealchaiwut/commander/issues/784) Unify structured logging: one schema, run_id everywhere — 2026-06-11
- [#785](https://github.com/zealchaiwut/commander/issues/785) Add cross-run log search and filter to Run Browser — 2026-06-11
- [#786](https://github.com/zealchaiwut/commander/issues/786) Add Cost tab: token usage per sprint/ticket/agent — 2026-06-11
- [#787](https://github.com/zealchaiwut/commander/issues/787) Redispatch hung agents with continuation context instead of idle-kill — 2026-06-11
- [#788](https://github.com/zealchaiwut/commander/issues/788) Ensure coder worktree freshness before each dispatch — 2026-06-11
- [#789](https://github.com/zealchaiwut/commander/issues/789) Route coder model by ticket size; add pre-dispatch doctor — 2026-06-11
- [#790](https://github.com/zealchaiwut/commander/issues/790) Route tester model by risk tier before dispatch — 2026-06-11
- [#791](https://github.com/zealchaiwut/commander/issues/791) Add per-area AGENTS.md context files (hierarchical) — 2026-06-11

## Sprint 58

- [#761](https://github.com/zealchaiwut/commander/issues/761) Enforce strangler-fig: gate server.py growth, extract first router — 2026-06-10
- [#762](https://github.com/zealchaiwut/commander/issues/762) Add log rotation and audit gitignore for secrets — 2026-06-10
- [#763](https://github.com/zealchaiwut/commander/issues/763) Add setup_machine.sh bootstrap script with doctor checks — 2026-06-10
- [#764](https://github.com/zealchaiwut/commander/issues/764) Track per-agent duration per issue in sprint runs — 2026-06-10
- [#765](https://github.com/zealchaiwut/commander/issues/765) Colorize issue numbers and agent names in log viewer — 2026-06-10
- [#766](https://github.com/zealchaiwut/commander/issues/766) Unify size-minutes map, fix XL default, SQLite calibration fallback — 2026-06-10

## Sprint 57

- [#752](https://github.com/zealchaiwut/commander/issues/752) Add `blocked` state to ticket state machine — 2026-06-10
- [#753](https://github.com/zealchaiwut/commander/issues/753) Refactor update_ticket.py to delegate status writes to transition() — 2026-06-10
- [#754](https://github.com/zealchaiwut/commander/issues/754) Enforce label lock during sprint run (RUN_MUTABLE_LABELS) — 2026-06-10
- [#755](https://github.com/zealchaiwut/commander/issues/755) Write-through transition() state to SQLite, drop verify-read — 2026-06-10
- [#756](https://github.com/zealchaiwut/commander/issues/756) Read dashboard state from local DB, not GitHub — 2026-06-10
- [#757](https://github.com/zealchaiwut/commander/issues/757) Persist sprint lifecycle and ticket order to DB — 2026-06-10
- [#758](https://github.com/zealchaiwut/commander/issues/758) Remove Neon dual-write; add one-shot export script — 2026-06-10
- [#759](https://github.com/zealchaiwut/commander/issues/759) Extend backup to authority DB via private repo — 2026-06-10
- [#760](https://github.com/zealchaiwut/commander/issues/760) Bootstrap full sync on first run from empty DB — 2026-06-10
- [#768](https://github.com/zealchaiwut/commander/issues/768) Scope Deploy tab to active project only — 2026-06-10
- [#769](https://github.com/zealchaiwut/commander/issues/769) Show and edit run folder and port on Deploy cards — 2026-06-10
- [#770](https://github.com/zealchaiwut/commander/issues/770) Show live log tail after Deploy or Restart — 2026-06-10
- [#771](https://github.com/zealchaiwut/commander/issues/771) Add Start/Stop controls and clarify Deploy vs Start — 2026-06-10
- [#772](https://github.com/zealchaiwut/commander/issues/772) Wire GH_TOKEN into launchd plist for headless gh auth — 2026-06-10

## Sprint 56

- [#731](https://github.com/zealchaiwut/commander/issues/731) Write urls.json manifest so sprint summary embeds screenshots inline — 2026-06-10
- [#732](https://github.com/zealchaiwut/commander/issues/732) Fix dead `?tab=status` deep-link after Status moved into Analytics — 2026-06-10
- [#735](https://github.com/zealchaiwut/commander/issues/735) Archive stale sprint files to reduce startup noise — 2026-06-10
- [#737](https://github.com/zealchaiwut/commander/issues/737) Add opt-in concurrent pipeline mode to sprint manager — 2026-06-10
- [#738](https://github.com/zealchaiwut/commander/issues/738) Serialize concurrent agent label transitions and develop merges — 2026-06-10
- [#739](https://github.com/zealchaiwut/commander/issues/739) Show dual active agents on sprint board in pipeline mode — 2026-06-10

## Sprint 55

- [#722](https://github.com/zealchaiwut/commander/issues/722) Add per-environment deploy config to project settings — 2026-06-10
- [#723](https://github.com/zealchaiwut/commander/issues/723) Add local deploy and restart actions for Mac-mini environments — 2026-06-10
- [#724](https://github.com/zealchaiwut/commander/issues/724) Generalize launchd installer for perf-coach UAT on Mac mini — 2026-06-10
- [#725](https://github.com/zealchaiwut/commander/issues/725) Integrate Render API for deploy and restart — 2026-06-10
- [#726](https://github.com/zealchaiwut/commander/issues/726) Add Deploy tab to project view — 2026-06-10
- [#727](https://github.com/zealchaiwut/commander/issues/727) Add Render-style env-var editor to project settings — 2026-06-10
- [#693](https://github.com/zealchaiwut/commander/issues/693) Add timeout to git rev-parse call in put_project_environments — 2026-06-10
- [#694](https://github.com/zealchaiwut/commander/issues/694) Rename `_pr_err` to `pr_err` in sprint_branch_status error logging — 2026-06-10
- [#704](https://github.com/zealchaiwut/commander/issues/704) Fix skip_estimator help text referring to documenter — 2026-06-10
- [#705](https://github.com/zealchaiwut/commander/issues/705) Audit external tools for compatibility with new documentor log format — 2026-06-10
- [#706](https://github.com/zealchaiwut/commander/issues/706) Strengthen idempotency check in document_issue.py — 2026-06-10
- [#707](https://github.com/zealchaiwut/commander/issues/707) Harden follow-up issue URL extraction in reviewer parsing — 2026-06-10
- [#708](https://github.com/zealchaiwut/commander/issues/708) Use configured model for gate failure analysis in #701 — 2026-06-10

## Sprint 54

- [#709](https://github.com/zealchaiwut/commander/issues/709) Wire agent-browser as Live Browser UAT Tester — 2026-06-10
- [#710](https://github.com/zealchaiwut/commander/issues/710) Run browser UAT steps via agent-browser, not MANUAL — 2026-06-10
- [#711](https://github.com/zealchaiwut/commander/issues/711) Tag agent-browser-testable UAT steps in BA tickets — 2026-06-10
- [#712](https://github.com/zealchaiwut/commander/issues/712) Attach browser step screenshots to UAT test reports — 2026-06-10
- [#713](https://github.com/zealchaiwut/commander/issues/713) Wire impeccable design skills into BA and coder agents — 2026-06-10
- [#715](https://github.com/zealchaiwut/commander/issues/715) Add Status and Trends sub-tabs to Analytics — 2026-06-10
- [#716](https://github.com/zealchaiwut/commander/issues/716) Move Sprint History into Sprint Mgmt tab as sub-view — 2026-06-10
- [#717](https://github.com/zealchaiwut/commander/issues/717) Move Notes to global left sidebar with local persistence — 2026-06-10
- [#718](https://github.com/zealchaiwut/commander/issues/718) Wire analytics calculations to local sprint/estimate files (drop Neon) — 2026-06-10
- [#719](https://github.com/zealchaiwut/commander/issues/719) Link activity log agent rows to GitHub issues — 2026-06-10
- [#720](https://github.com/zealchaiwut/commander/issues/720) Emit activity event on every label transition — 2026-06-10
- [#721](https://github.com/zealchaiwut/commander/issues/721) Emit scoped activity-log events for sprint lifecycle — 2026-06-10

## Sprint 1

- [#696](https://github.com/zealchaiwut/commander/issues/696) Skip estimator by default when running sprint — use `--no-skip-estimator` to opt in — 2026-06-09
- [#697](https://github.com/zealchaiwut/commander/issues/697) Run documentor once per sprint after all tickets merge, not per ticket — 2026-06-09
- [#698](https://github.com/zealchaiwut/commander/issues/698) Extend documentor to update `docs/quickstart.md` and `docs/tutorial.md` guide files — 2026-06-09
- [#699](https://github.com/zealchaiwut/commander/issues/699) Run BA rewrite and estimator on reviewer-created follow-up tickets automatically — 2026-06-09
- [#700](https://github.com/zealchaiwut/commander/issues/700) Make agent models configurable per-agent or globally via `agent_config` in `sprint.yaml` — 2026-06-09
- [#701](https://github.com/zealchaiwut/commander/issues/701) Enrich gate failure comments with root cause analysis from stored failure records — 2026-06-09

## Sprint 53

- [#688](https://github.com/zealchaiwut/commander/issues/688) Extract date-param validation to helper function — 2026-06-09
- [#687](https://github.com/zealchaiwut/commander/issues/687) Document finish-card API breaking change (404 → 200 no_data) — 2026-06-09
  - `GET /api/sprints/{label}/finish-card` now always returns HTTP 200.
  - When a sprint has never been run it returns `state: "no_data"` instead of HTTP 404.
  - Clients must check `response.state === "no_data"` rather than HTTP status codes.
  - Full response shapes (no_data / running / completed / has_rework / cancelled) documented in `docs/features/api.md`.
- [#681](https://github.com/zealchaiwut/commander/issues/681) Add Scaffold Docs action to Project Settings — 2026-06-09
- [#670](https://github.com/zealchaiwut/commander/issues/670) Add error logging to sprint PR lookup in finish sprint — 2026-06-09
- [#667](https://github.com/zealchaiwut/commander/issues/667) Add symlink traversal test cases to test_643 — 2026-06-09
- [#664](https://github.com/zealchaiwut/commander/issues/664) Improve git repository validation in put_project_environments — 2026-06-09
- [#663](https://github.com/zealchaiwut/commander/issues/663) Fix symlink escape vulnerability in /api/fs/list endpoint — 2026-06-09

## Sprint 52

- [#672](https://github.com/zealchaiwut/commander/issues/672) Fix API Error on Finish Card Submission — 2026-06-09
- [#671](https://github.com/zealchaiwut/commander/issues/671) Sync sprint progress across all three pill components — 2026-06-09
- [#660](https://github.com/zealchaiwut/commander/issues/660) Fix Multiple Drag-and-Drop Selection Not Working — 2026-06-09
- [#658](https://github.com/zealchaiwut/commander/issues/658) Clear Empty Sprints Up To First Active Sprint — 2026-06-09
- [#657](https://github.com/zealchaiwut/commander/issues/657) Add Sprint and Agent Logs to Activity Tab — 2026-06-09
- [#656](https://github.com/zealchaiwut/commander/issues/656) Fix Bulk Create Status Stuck After Server Restart — 2026-06-09
- [#651](https://github.com/zealchaiwut/commander/issues/651) Add GET analytics/metrics endpoint for projects — 2026-06-09
- [#650](https://github.com/zealchaiwut/commander/issues/650) Build Analytics page with Calibration tab — 2026-06-09
- [#649](https://github.com/zealchaiwut/commander/issues/649) Add calibration analytics endpoint for ticket sizing — 2026-06-09
- [#648](https://github.com/zealchaiwut/commander/issues/648) Build Metrics tab with ANL-3 data cards — 2026-06-09
- [#674](https://github.com/zealchaiwut/commander/issues/674) Fix Duplicate Estimation Labels on Ticket View — 2026-06-09
- [#673](https://github.com/zealchaiwut/commander/issues/673) Limit visible tags to 10 most recently used — 2026-06-09
- [#659](https://github.com/zealchaiwut/commander/issues/659) Fix false failure when tester subprocess exits 0 — 2026-06-09

## Sprint 51

- #637: Add project_events table and recorder to dashboard DB
- #638: Add settings KV table and sprint_tickets.estimated_size
- #639: Add Settings REST API with effective read and override write
- #640: Estimator reads per-project config and writes estimated_size
- #641: Build global settings screen behind header gear icon
- #642: Build Project Settings tab under More
- #643: Add editable env paths with server-side folder browser
- #644: Add directional settings sync with diff preview

## Sprint 24

- #244: Add Neon DB connection module and Alembic scaffolding
- #245: Add SQLAlchemy models and migration for sprints + sprint_tickets
- #246: Add sprint repository layer for DB access
- #247: Dual-write sprint state to Neon and JSON
- #248: Add one-shot backfill script for sprints to Neon
- #325: Add structured JSON-lines logging module (disk-first)
- #326: Mint run_id at all agent entry points
- #327: Migrate failure-path print()s to structured logger
- #328: Fix missing .env entry in .gitignore
- #329: Add /api/version endpoint and surface build stamp in sidebar
- #330: Backup project identity fields from projects.json to Neon
- #331: Fix estimation failure on bulk create operation
- #335: Recreating Failed Ticket Creates Empty Issue Instead
- #336: Show label and attachment warnings after draft generation
- #337: Add Delete Action for Selected Closed/Unplanned Issues
- #339: Replace Goal Field with Sprint Label on New Sprint Creation
- #340: Auto-create next sprint on drag below last sprint
- #344: [follow-up] app.js: Add timeout to preview endpoint fetch in rerun modal
- #345: [follow-up] check_neon_connection.py: Consolidate psycopg2 import error handling
- #346: [follow-up] app.js: Remove unused RERUN_STRIP_LABELS constant

## Sprint 98.1

Follow-up hardening pass (re-run of sprint-98) across the timeline projection, model routing, sprint-label resolution, and frontend design-token/accessibility plumbing. The sprint-timeline `projected_finish` math is corrected: `running_remaining_min` is renamed to `running_remaining` and serial mode now *sums* every running issue's remaining time (both must finish) while pipeline mode takes the *max* (they overlap), fixing an understated finish estimate in serial dispatch (#1167); the `timeline_service` data helpers (`_get_sprint_issues`, `_get_agent_runs`, `_get_settings`, `_get_sprint_row`, `_get_calibration_records`, `_get_launch_issue_order`) now log a WARNING with traceback instead of silently swallowing exceptions (#1169). Coder model routing gains `.yml` in `_DOCS_PATH_EXTENSIONS` so `.yml`-only changes route as docs/config alongside `.yaml`/`.md`/`.json` (#1428), and serial dispatch now sets `issue_state.coder_routing_reason` so the running-pane coder badge shows its routing-reason tooltip (#1427). `rebuild_calibration_cache` threads `DB_PATH` into `_calibration_absorb_state_file` so an explicit cache rebuild uses the SQLite size-label fallback (#1364). Sprint-label resolution is hardened against zombie sprints: `_all_sprint_label_names` now intersects mirror-derived sprint labels against the live `gh` label registry, so a deleted sprint label lingering in stale closed-issue mirror rows no longer resurrects in `/api/sprints` — falling back to the unfiltered mirror only when the live list is empty (gh down / offline) (#1355). The mis-sizing history rebuild (`rebuild_mis_sizing_history`) now logs a WARNING on a `gh issue list` non-zero exit or exception instead of swallowing it, and returns a `labels_fetched` flag in the rebuild response (#1396). On the frontend, the home project badge validates the project color against a fixed palette and falls back to the gray class for non-palette values (#1174); the top-level tab strip's roving tabindex now uses a `_GROUP_CHILDREN` map and a new exported `computeRovingTabindex()` helper so a dropdown-group trigger (manage/planning) stays keyboard-reachable when one of its child tabs is active, fixing the case where no tab was Tab-focusable (#1175); `project.html`'s inline `:root` token block that shadowed the canonical `tokens.css` is removed (only the page-specific `--sidebar-width` remains) (#1200); and impeccable rule suppressions are scoped per-file via `impeccable-disable` comments in `project.html`/`project-todo.js` instead of disabled globally (#1205). The test artifact `.commander/settings_store.json` accidentally committed under #1342 is removed from version control and gitignored (#1359). No schema or endpoint changes — correctness, observability, accessibility, and cleanup only.

- [#1167](https://github.com/zealchaiwut/commander/issues/1167) Fix misleading running_remaining_min name and serial-mode summing in timeline projected_finish — 2026-06-25
- [#1169](https://github.com/zealchaiwut/commander/issues/1169) Log swallowed exceptions in timeline_service data helpers — 2026-06-25
- [#1174](https://github.com/zealchaiwut/commander/issues/1174) Home project badge: fall back to default color class for non-palette values — 2026-06-25
- [#1175](https://github.com/zealchaiwut/commander/issues/1175) Roving tabindex: keep dropdown-group tab focusable via computeRovingTabindex helper — 2026-06-26
- [#1200](https://github.com/zealchaiwut/commander/issues/1200) Consolidate design tokens — remove inline :root block shadowing tokens.css in project.html — 2026-06-26
- [#1205](https://github.com/zealchaiwut/commander/issues/1205) Scope impeccable rule suppressions per-file instead of disabling globally — 2026-06-26
- [#1355](https://github.com/zealchaiwut/commander/issues/1355) Zombie sprints: filter mirror sprint labels against live registry to drop deleted labels — 2026-06-26
- [#1359](https://github.com/zealchaiwut/commander/issues/1359) Remove accidentally committed .commander/settings_store.json test artifact — 2026-06-26
- [#1364](https://github.com/zealchaiwut/commander/issues/1364) Thread db_path into rebuild_calibration_cache for SQLite size-label fallback — 2026-06-26
- [#1396](https://github.com/zealchaiwut/commander/issues/1396) Surface GitHub fetch failures in mis-sizing history rebuild — 2026-06-26
- [#1427](https://github.com/zealchaiwut/commander/issues/1427) Serial dispatch sets coder_routing_reason for badge tooltip — 2026-06-26
- [#1428](https://github.com/zealchaiwut/commander/issues/1428) Add .yml to docs-only routing extensions — 2026-06-26












































































































