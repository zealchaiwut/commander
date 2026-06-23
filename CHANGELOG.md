# Changelog

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
