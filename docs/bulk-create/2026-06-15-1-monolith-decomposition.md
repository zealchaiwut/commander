# Monolith decomposition — server.py + sprint_manager.py

**Date:** 2026-06-15
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

## Notes

**Goal:** shrink `server.py` (14,356 lines / 166 routes) and `sprint_manager.py` (~10k lines)
via pure moves — same logic, no behaviour changes. S/M tickets only; never one XL "move
everything" ticket.

**Invariants (every ticket):** no logic change (cut-paste + import wiring); no new routes
in `server.py` (`COMMANDER_GATE_MONOLITH` stays green); follow router → `*_service` → repo;
`python -m py_compile` on touched files; dashboard imports clean; `sprint_manager.py --help`
works; smoke tests green.

**Rollout:** Sprint N — leaf S tickets (A1–A5, B1–B7). Sprint N+1 — M service groups
(A6–A13, B8–B13). Sprint N+2 — pre-split big clusters (A14–A16, B14–B18) one PR each.
Close-out — A17 + B18 thinning; route inventory diff = 0.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Extract system/health/version routes from server.py to routers/system.py. Move GET /api/health, /api/environment, /api/version, /api/gh-auth-status, /api/repo/config, /api/github/labels* and thin wrappers. Read-only cluster, zero deps — first move. Acceptance: routes behave identically; no new routes in server.py; py_compile clean; route count unchanged.
---
Extract HTML page and static serving from server.py to routers/pages.py. Move GET /, /home, /overview, /project/* page handlers and version-hash injection helper. Pure serving only. Acceptance: pages load identically; no new routes in server.py; py_compile clean; route count unchanged.
---
Extract project CRUD routes from server.py to routers/projects.py. Move GET/POST /api/projects and DELETE /api/projects/{owner}/{repo}. Delegate to existing projects data module — move route shells only. Acceptance: project CRUD unchanged; no new routes in server.py; py_compile clean.
---
Extract project branch routes from server.py to routers/project_branches.py. Move /api/projects/{owner}/{repo}/branches/* (get_stale_branches, delete_project_branch). Small isolated cluster. Acceptance: branch endpoints unchanged; no new routes in server.py; py_compile clean.
---
Extract issue-approval routes from server.py to routers/issues.py. Move /api/issues/{id}/approve|reject|close, GET /api/issues/{id}/test-report, GET /api/issues. Issue state transitions and test-report fetch. Acceptance: issue endpoints unchanged; no new routes in server.py; py_compile clean.
---
Extract analytics and metrics routes from server.py to routers/sprint_analytics.py. Move /api/sprints/{label}/estimate-summary|estimate|outcome|estimate-vs-actual, /api/estimates/batch, /api/calibration, /api/metrics/sprints. Delegate to estimates JSON and token_usage; finish partial outcome service move. Acceptance: analytics endpoints unchanged; no new routes in server.py; py_compile clean.
---
Extract mis-sizing routes from server.py to routers/mis_sizing.py. Move /api/sprints/{label}/mis-sizing-flags* and /api/mis-sizing/*; call existing _mis_sizing module. Acceptance: mis-sizing endpoints unchanged; no new routes in server.py; py_compile clean.
---
Extract preflight and DAG routes from server.py to routers/sprint_preflight.py. Move /api/sprints/{label}/preflight, preflight-fix, cycle-check, conflicts, dep-order. Uses _dag_build and cycle detection. Acceptance: preflight endpoints unchanged; no new routes in server.py; py_compile clean.
---
Extract sprint state, live, and log routes from server.py to routers/sprint_live.py. Move GET /api/sprints/{label}/state*|live*, issue/{num}/log, /api/logs/runs, POST /api/logs/sync-github. HTTP delegation to state JSON and SSE. Acceptance: live/log endpoints unchanged; no new routes in server.py; py_compile clean.
---
Extract planning and nav routes from server.py to routers/sprint_planning.py. Move /api/sprint-nav-status, /api/sprint-progress, /api/sprint-nav-summary, /api/sprint-planning/*, /api/open-issues, POST /api/issues/{id}/sprint-label. Keep _settled_done_from_columns with sprint-progress. Acceptance: planning/nav endpoints unchanged; no new routes in server.py; py_compile clean.
---
Extract sprint CRUD routes from server.py to routers/sprint_crud.py. Move POST /api/sprints/create|{label}/rename|{label}/tickets/reorder|{label}/plan, DELETE /api/sprints/{label}, delete-empty, cleanup-empty. Label and branch ops. Acceptance: sprint CRUD unchanged; no new routes in server.py; py_compile clean.
---
Extract timeline, summaries, and home routes from server.py to routers/sprint_summaries.py. Move GET /api/sprints/timeline|summaries, /api/sprint-history*, GET/POST /api/sprint-status, /api/sprint-summary, /api/home. Acceptance: summary/home endpoints unchanged; no new routes in server.py; py_compile clean.
---
Extract alerts, docs, deploy-overview, and misc routes from server.py to routers/system_misc.py. Move /api/alerts*, /api/docs-freshness/*, /api/deploy/overview, /api/maintenance/sprints/cleanup, /api/plan-usage, /api/estimator/health, POST /api/issues/{id}/estimate. Acceptance: misc endpoints unchanged; no new routes in server.py; py_compile clean.
---
Extract finish and bulk-complete preview routes from server.py to routers/sprint_finish.py (read paths). Move finish-preview and bulk-complete-preview read/preview handlers only; leave write paths for next ticket if split. Acceptance: preview endpoints unchanged; no new routes in server.py; py_compile clean.
---
Extract finish and bulk-complete write routes from server.py to routers/sprint_finish.py. Move POST finish and POST bulk-complete orchestration; colocate with preview routes from prior ticket. Acceptance: finish/bulk-complete writes unchanged; no new routes in server.py; py_compile clean.
---
Extract sprint run read/preview routes from server.py to routers/sprint_run.py. Move branch-status, rerun-preview, GET .../rerun, deploy/promote, reports/daily — read/preview half of the ~1.2k-line cluster. Acceptance: read routes unchanged; no new routes in server.py; py_compile clean.
---
Extract sprint run write/dispatch routes from server.py to sprint_run_service.py + routers/sprint_run.py. Move POST /api/sprints/run, POST .../rerun, DELETE /api/sprints/run/{label} and subprocess glue into sprint_run_service.py; wire router to service. Acceptance: run/rerun/cancel unchanged; no new routes in server.py; py_compile clean.
---
Extract bulk-ticket job lifecycle reads from server.py to routers/bulk_tickets.py. Move GET /api/tickets/bulk/{job_id}* and _get_bulk_job loader — first third of bulk state machine. Acceptance: bulk job reads unchanged; no new routes in server.py; py_compile clean.
---
Extract bulk-ticket draft/create routes from server.py to routers/bulk_tickets.py. Move POST /api/tickets/draft|create|bulk and post-selected — second third of bulk state machine. Acceptance: draft/create unchanged; no new routes in server.py; py_compile clean.
---
Extract bulk-ticket per-job action routes from server.py to routers/bulk_tickets.py. Move skip|retry|redraft|estimate|retry-with-*|size-remedy-* per-job actions — final third of bulk state machine. Acceptance: per-job actions unchanged; no new routes in server.py; py_compile clean.
---
Reduce server.py to thin app factory after A1–A16. Leave only app construction, middleware, lifespan, and include_router wiring. Target under 400 lines. Delete moved code; verify /api route inventory diff is zero. Acceptance: server.py under 400 lines; all routes served via routers; COMMANDER_GATE_MONOLITH green; full smoke pass.
---
Extract sprint_manager data classes to services/sprint_manager/state.py. Move IssueState, SprintState, GateResult, SprintSummary. Leaf module, no deps. Acceptance: pure move; sprint_manager.py --help works; py_compile clean; imports updated.
---
Extract sprint_manager config loading to services/sprint_manager/config.py. Move SprintConfig, load_config, discover_config, _default_config, _resolve_path (~350 lines, mechanical). Acceptance: pure move; config discovery unchanged; sprint_manager.py --help works.
---
Extract sprint_manager path/constant helpers to services/sprint_manager/paths.py. Move _pid_file_path, _plan_json_path, _state_path, _summary_path, _sprint_number, _label_base, etc. Pure string/path ops. Acceptance: pure move; path resolution unchanged; py_compile clean.
---
Extract sprint_manager alert channels to services/sprint_manager/alerts.py. Move HangDetector, dispatch_alerts, _alert_dashboard_banner|email|discord|ntfy|file. Acceptance: pure move; alert dispatch unchanged; py_compile clean.
---
Extract sprint_manager event emission to services/sprint_manager/events.py. Move _emit_sprint_lifecycle_event, _failure_event_detail, _emit_ticket_failed, _post_agent_event, _post_sprint_status. Acceptance: pure move; events unchanged; py_compile clean.
---
Extract sprint_manager model routing to services/sprint_manager/model_routing.py. Move _resolve_coder_model, _effective_coder_backend, _select_coder_backend, size/risk routing. Acceptance: pure move; model selection unchanged; py_compile clean.
---
Extract sprint_manager time/PID/pause helpers to services/sprint_manager/timekeeping.py. Move _token_window_sums, _utcnow, _bangkok_now, _wait_if_paused, _setup_pid_file, _acquire/_release_pid_lock. Acceptance: pure move; timekeeping unchanged; py_compile clean.
---
Extract sprint_manager DB-write helpers to services/sprint_manager/db_writes.py. Move _sprint_db_set_state_sm, _db_agent_start_sm, _db_agent_finish_sm, _db_ingest_run_sm, _plan_json_set_state_sm. Serialized SQLite writes. Acceptance: pure move; DB writes unchanged; py_compile clean.
---
Extract sprint_manager failure handling to services/sprint_manager/failures.py. Move record_failure, _build_failure_suffix, FailureCategory, _generate_gate_failure_analysis, _publish_gate_failure_analyses. Acceptance: pure move; failure handling unchanged; py_compile clean.
---
Extract sprint_manager pytest/lint gates to services/sprint_manager/gates.py (part 1). Move _gate_pytest, _gate_lint (+ _lint_autofix_commit), _run_frontend_lint, _changed_*_files from the ~1.1k-line gates cluster. Acceptance: pure move; gates behave identically; py_compile clean.
---
Extract sprint_manager typecheck/design/merge/monolith gates to services/sprint_manager/gates.py (part 2). Move _gate_typecheck, _gate_design, _gate_merge_preview, _gate_monolith, _run_quality_gates orchestrator. Acceptance: pure move; quality gate orchestration unchanged; py_compile clean.
---
Extract sprint_manager label transitions to services/sprint_manager/label_transitions.py. Move _get_issue_labels, _current_status_labels, _sweep_stale_status, _transition_safe, _add_blocked_label, _emit_label_transition_event. Acceptance: pure move; label transitions unchanged; py_compile clean.
---
Extract sprint_manager worktree/env helpers to services/sprint_manager/worktree.py. Move _resolve_uat_env_for_tester, _worktree_hygiene, _crg_update_worktree, _stash_to_quarantine, _detect_port. Acceptance: pure move; worktree ops unchanged; py_compile clean.
---
Extract sprint_manager feature-branch and post-tester logic to services/sprint_manager/feature_tracking.py. Move _find_feature_branch, _is_branch_merged_into, _was_feature_merged_via_log, handle_post_tester. Acceptance: pure move; feature tracking unchanged; py_compile clean.
---
Extract sprint_manager coder dispatch to services/sprint_manager/dispatch.py (part 1). Move _dispatch_coder, _load_agent_persona, _agent_identity_env (~453 lines). Acceptance: pure move; coder dispatch unchanged; py_compile clean.
---
Extract sprint_manager tester/doctor dispatch to services/sprint_manager/dispatch.py (part 2). Move _dispatch_tester, _doctor_probe_auth, _dispatch_doctor (~516 lines). Acceptance: pure move; tester dispatch unchanged; py_compile clean.
---
Extract sprint_manager summary generation to services/sprint_manager/summary.py. Move generate_sprint_summary, write_sprint_summary, create_summary_github_issue, _prompt_learnings. Acceptance: pure move; summary generation unchanged; py_compile clean.
---
Extract sprint_manager post-sprint agents to services/sprint_manager/post_sprint.py. Move _create_sprint_pr, _dispatch_documenter, _dispatch_reviewer, _dispatch_ba_for_followup, _dispatch_estimator_for_followup, _enrich_followup_tickets. Acceptance: pure move; post-sprint agents unchanged; py_compile clean.
---
Extract sprint_manager pipeline dispatch to services/sprint_manager/pipeline.py. Move _run_pipeline_dispatch, _compute_dispatch_levels, _build_sprint_dag_layers, _warn_file_conflicts, list_backlog_issues. Acceptance: pure move; pipeline dispatch unchanged; py_compile clean.
---
Extract run_sprint preflight and branch-setup into run_sprint_preflight() helper. After B1–B17, pull the preflight + branch-setup portion of run_sprint (~1,237 lines today) into a dedicated helper module/function; run_sprint calls it. Do not move run_sprint as one blob. Acceptance: pure refactor extract; run behaviour unchanged; sprint_manager.py --help works; smoke pass.
---
Extract run_sprint per-ticket loop into run_sprint_loop() helper. Pull the per-ticket loop body into run_sprint_loop(); leave run_sprint as a sequence of phase calls targeting ~150 lines. Acceptance: pure refactor extract; run behaviour unchanged; run_sprint reads as phase orchestration; smoke pass.
```

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
