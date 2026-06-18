# Changelog

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
