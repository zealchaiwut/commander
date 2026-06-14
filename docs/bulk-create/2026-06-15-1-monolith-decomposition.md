# Bulk Create — Monolith Decomposition (server.py + sprint_manager.py)

> **Goal:** shrink the two files that get edited every sprint and break on every
> change. **Pure moves, same logic** — extract cohesive function groups into
> modules behind stable imports. No behaviour changes; discuss redesign later.
>
> **Sizing rule:** S/M only. Anything that looks L below is **split into 2–3
> S/M tickets** — never ship an XL "move the whole thing" ticket.
>
> Current sizes (2026-06-15): `server.py` 14,356 lines / 166 routes ·
> `sprint_manager.py` 9,998 lines · 51 router modules already extracted under
> `apps/dashboard/routers/`.

## Invariants every ticket must hold (put in each issue's AC)

- **No logic change.** Diff is cut-paste + import wiring only. If a behaviour
  changes, the ticket is wrong.
- **No new routes in `server.py`** — the `COMMANDER_GATE_MONOLITH` gate must
  stay green. New routes live in `routers/`.
- Follow the existing **router → `*_service` → repo** split already used by the
  51 modules in `routers/`.
- After the move: `python -m py_compile` the touched files; dashboard imports
  clean; `sprint_manager.py --help` works; smoke suite green.
- One cluster per ticket. Keep PRs small so a bad move reverts cleanly.

## Sequencing

Do the **leaf** tickets first (no deps): data classes, config, constants,
pure helpers. Then the cohesive service groups. The big orchestrators
(`run_sprint`, bulk-ticket job machine, `/api/sprints/run`) are **last** and
are pre-split into pieces below.

---

## Batch A — server.py extractions

### A1 — Extract system/health/version routes to `routers/system.py` (S)
Move `/api/health`, `/api/environment`, `/api/version`, `/api/gh-auth-status`,
`/api/repo/config`, `/api/github/labels*` and their thin wrappers. Read-only,
zero deps — ideal first move.

### A2 — Extract HTML page + static serving to `routers/pages.py` (S)
Move `GET /`, `/home`, `/overview`, `/project/*` page handlers and the version-
hash injection helper. Pure serving.

### A3 — Extract project CRUD routes to `routers/projects.py` (S)
`GET/POST /api/projects`, `DELETE /api/projects/{owner}/{repo}`. Delegates to the
existing `projects` data module — move the route shells only.

### A4 — Extract project branch routes to `routers/project_branches.py` (S)
`/api/projects/{owner}/{repo}/branches/*` (`get_stale_branches`,
`delete_project_branch`). Small, isolated.

### A5 — Extract issue-approval routes to `routers/issues.py` (S)
`/api/issues/{id}/approve|reject|close`, `GET /api/issues/{id}/test-report`,
`GET /api/issues`. Issue state transitions + test-report fetch.

### A6 — Extract analytics/metrics routes to `routers/sprint_analytics.py` (M)
`/api/sprints/{label}/estimate-summary|estimate|outcome|estimate-vs-actual`,
`/api/estimates/batch`, `/api/calibration`, `/api/metrics/sprints`. Delegates to
estimates JSON + `token_usage`. (`outcome` already partly in a service — finish
the move.)

### A7 — Extract mis-sizing routes to `routers/mis_sizing.py` (S)
`/api/sprints/{label}/mis-sizing-flags*`, `/api/mis-sizing/*`. Calls the existing
`_mis_sizing` module.

### A8 — Extract preflight + DAG routes to `routers/sprint_preflight.py` (M)
`/api/sprints/{label}/preflight`, `preflight-fix`, `cycle-check`, `conflicts`,
`dep-order`. Uses `_dag_build` + cycle detection.

### A9 — Extract sprint state/live/log routes to `routers/sprint_live.py` (M)
`GET /api/sprints/{label}/state*|live*`, `issue/{num}/log`, `/api/logs/runs`,
`POST /api/logs/sync-github`. HTTP delegation to state JSON + SSE.

### A10 — Extract planning/nav routes to `routers/sprint_planning.py` (M)
`/api/sprint-nav-status`, `/api/sprint-progress`, `/api/sprint-nav-summary`,
`/api/sprint-planning/*`, `/api/open-issues`, `POST /api/issues/{id}/sprint-label`.
(Keep `_settled_done_from_columns` with `sprint-progress`.)

### A11 — Extract sprint CRUD routes to `routers/sprint_crud.py` (M)
`POST /api/sprints/create|{label}/rename|{label}/tickets/reorder|{label}/plan`,
`DELETE /api/sprints/{label}`, `delete-empty`, `cleanup-empty`. Label/branch ops.

### A12 — Extract timeline/summaries/home to `routers/sprint_summaries.py` (M)
`GET /api/sprints/timeline|summaries`, `/api/sprint-history*`,
`GET/POST /api/sprint-status`, `/api/sprint-summary`, `/api/home`.

### A13 — Extract alerts/docs/deploy-overview/misc to `routers/system_misc.py` (M)
`/api/alerts*`, `/api/docs-freshness/*`, `/api/deploy/overview`,
`/api/maintenance/sprints/cleanup`, `/api/plan-usage`, `/api/estimator/health`,
`POST /api/issues/{id}/estimate`.

### A14 — Split finish/bulk-complete routes into `routers/sprint_finish.py` (M)
`finish-preview`, `POST finish`, `bulk-complete-preview`, `POST bulk-complete`.
**If the orchestration body is large, split A14 into A14a (preview/read paths,
S) + A14b (the finish/bulk-complete write paths, M).**

### A15 — Split `/api/sprints/run|rerun|branch-status` into `routers/sprint_run.py` (M ×2)
This is the biggest server cluster (~1.2k lines). **Split into:**
- **A15a (M):** read/preview routes — `branch-status`, `rerun-preview`,
  `GET .../rerun`, `deploy/promote`, `reports/daily`.
- **A15b (M):** the write/dispatch routes — `POST /api/sprints/run`,
  `POST .../rerun`, `DELETE /api/sprints/run/{label}` + their subprocess glue
  into a `sprint_run_service.py`.

### A16 — Split the bulk-ticket job machine into `routers/bulk_tickets.py` (M ×3)
~2.5k lines of bulk-create state machine. **Split into three:**
- **A16a (M):** job lifecycle reads — `GET /api/tickets/bulk/{job_id}*`,
  `_get_bulk_job` loader.
- **A16b (M):** draft/create — `POST /api/tickets/draft|create|bulk`,
  `post-selected`.
- **A16c (M):** per-job actions — `skip|retry|redraft|estimate|retry-with-*|
  size-remedy-*`.

### A17 — Reduce `server.py` to a thin app-factory (M)
After A1–A16: leave only app construction, middleware, lifespan, and
`include_router` wiring. Target **< 400 lines**. Pure deletion of moved code +
import list. Verify route count unchanged (`/api` inventory diff = 0).

---

## Batch B — sprint_manager.py extractions

### B1 — Extract data classes to `services/sprint_manager/state.py` (S)
`IssueState`, `SprintState`, `GateResult`, `SprintSummary`. Leaf, no deps.

### B2 — Extract config loading to `config.py` (S)
`SprintConfig`, `load_config`, `discover_config`, `_default_config`,
`_resolve_path`. (~350 lines, but mechanical.)

### B3 — Extract path/constant helpers to `paths.py` (S)
`_pid_file_path`, `_plan_json_path`, `_state_path`, `_summary_path`,
`_sprint_number`, `_label_base`, etc. Pure string/path ops.

### B4 — Extract alert channels to `alerts.py` (S)
`HangDetector`, `dispatch_alerts`, `_alert_dashboard_banner|email|discord|ntfy|
file`.

### B5 — Extract event emission to `events.py` (S)
`_emit_sprint_lifecycle_event`, `_failure_event_detail`, `_emit_ticket_failed`,
`_post_agent_event`, `_post_sprint_status`.

### B6 — Extract model routing to `model_routing.py` (S)
`_resolve_coder_model`, `_effective_coder_backend`, `_select_coder_backend`,
size/risk routing.

### B7 — Extract time/PID/pause helpers to `timekeeping.py` (S)
`_token_window_sums`, `_utcnow`, `_bangkok_now`, `_wait_if_paused`,
`_setup_pid_file`, `_acquire/_release_pid_lock`.

### B8 — Extract DB-write helpers to `db_writes.py` (M)
`_sprint_db_set_state_sm`, `_db_agent_start_sm`, `_db_agent_finish_sm`,
`_db_ingest_run_sm`, `_plan_json_set_state_sm`. Serialized SQLite writes.

### B9 — Extract failure handling to `failures.py` (M)
`record_failure`, `_build_failure_suffix`, `FailureCategory`,
`_generate_gate_failure_analysis`, `_publish_gate_failure_analyses`.

### B10 — Extract quality gates to `gates.py` (M ×2)
~1.1k lines. **Split:**
- **B10a (M):** `_gate_pytest`, `_gate_lint` (+ the new `_lint_autofix_commit`),
  `_run_frontend_lint`, `_changed_*_files`.
- **B10b (M):** `_gate_typecheck`, `_gate_design`, `_gate_merge_preview`,
  `_gate_monolith`, `_run_quality_gates` orchestrator.

### B11 — Extract label transitions to `label_transitions.py` (M)
`_get_issue_labels`, `_current_status_labels`, `_sweep_stale_status`,
`_transition_safe`, `_add_blocked_label`, `_emit_label_transition_event`.

### B12 — Extract worktree/env to `worktree.py` (M)
`_resolve_uat_env_for_tester`, `_worktree_hygiene`, `_crg_update_worktree`,
`_stash_to_quarantine`, `_detect_port`.

### B13 — Extract feature-branch + post-tester to `feature_tracking.py` (M)
`_find_feature_branch`, `_is_branch_merged_into`, `_was_feature_merged_via_log`,
`handle_post_tester`.

### B14 — Extract agent dispatch to `dispatch.py` (M ×2)
`_dispatch_coder` (~453) + `_dispatch_tester` (~516) are big. **Split:**
- **B14a (M):** `_dispatch_coder` + `_load_agent_persona` + `_agent_identity_env`.
- **B14b (M):** `_dispatch_tester` + `_doctor_probe_auth` + `_dispatch_doctor`.

### B15 — Extract summary generation to `summary.py` (M)
`generate_sprint_summary`, `write_sprint_summary`, `create_summary_github_issue`,
`_prompt_learnings`.

### B16 — Extract post-sprint agents to `post_sprint.py` (M)
`_create_sprint_pr`, `_dispatch_documenter`, `_dispatch_reviewer`,
`_dispatch_ba_for_followup`, `_dispatch_estimator_for_followup`,
`_enrich_followup_tickets`.

### B17 — Extract pipeline dispatch to `pipeline.py` (M)
`_run_pipeline_dispatch`, `_compute_dispatch_levels`, `_build_sprint_dag_layers`,
`_warn_file_conflicts`, `list_backlog_issues`.

### B18 — Thin `run_sprint` to an orchestrator (M ×2)
`run_sprint` is ~1,237 lines. **Do NOT move as one ticket.** After B1–B17 the
body should already shrink (it now calls the extracted services). **Split:**
- **B18a (M):** extract the preflight + branch-setup portion into
  `run_sprint_preflight()` helper.
- **B18b (M):** extract the per-ticket loop body into a `run_sprint_loop()`
  helper; leave `run_sprint` as the sequence of phase calls.
Target: `run_sprint` reads as ~150 lines of phase calls.

---

## Suggested rollout

1. **Sprint N:** all leaf S tickets (A1–A5, B1–B7) — parallel-safe, mechanical.
2. **Sprint N+1:** the M service groups (A6–A13, B8–B13).
3. **Sprint N+2:** the pre-split big ones (A14–A16, B14–B18) one at a time, each
   behind its own PR + full smoke.
4. **Close-out:** A17 + B18 thinning, confirm route inventory + `--help` unchanged.

After this, the two hot files stop being merge-conflict magnets and a typo in
one cluster can't break the others.
