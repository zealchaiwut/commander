# Schema

Commander uses two persistence layers:

- **SQLite** (`apps/dashboard/commander.db`) — local agent event history for the dashboard
- **Neon (Postgres)** — sprint state, ticket state, and project registry; managed via Alembic

## Neon Tables (Postgres)

Run `alembic upgrade head` to apply all migrations.

### sprints

Stores sprint-level state for each project.

| Column | Type | Description |
|---|---|---|
| `id` | integer PK | Auto-increment |
| `label` | text NOT NULL | Sprint label, e.g. `sprint-24` (unique) |
| `goal` | text NOT NULL | Sprint goal description |
| `status` | enum NOT NULL | `pending`, `running`, `complete`, `cancelled` |
| `project` | text NOT NULL | Repo slug, e.g. `zealchaiwut/commander` |
| `created_at` | timestamptz | Defaults to `now()` |
| `started_at` | timestamptz | Set when sprint transitions to `running` |
| `completed_at` | timestamptz | Set when sprint transitions to `complete` |
| `cancelled_at` | timestamptz | Set when sprint transitions to `cancelled` |

### sprint_tickets

Stores per-ticket state within a sprint. Cascade-deletes with `sprints`.

| Column | Type | Description |
|---|---|---|
| `id` | integer PK | Auto-increment |
| `sprint_id` | integer FK → sprints.id | Parent sprint (ON DELETE CASCADE) |
| `issue_number` | integer NOT NULL | GitHub issue number |
| `position` | integer NOT NULL | Ordering within the sprint |
| `status` | enum NOT NULL | `pending`, `running`, `done`, `failed`, `skipped` |
| `started_at` | timestamptz | Set when ticket starts running |
| `completed_at` | timestamptz | Set when ticket finishes |
| `agent_active` | text | Active agent role when ticket is running |
| `actual_elapsed_seconds` | integer | Total wall-clock seconds for completed ticket |
| `total_tokens` | integer | Cumulative tokens consumed by agents for this ticket |
| `estimated_size` | text | Size label from estimator (`S`, `M`, `L`, `XL`); nullable |

Index: `ix_sprint_tickets_sprint_position` on `(sprint_id, position)`.
Unique constraint: `(sprint_id, issue_number)`.

### settings

Key-value store for project-level config overrides. Supports global defaults and per-project overrides with shallow merge semantics (project fields win over global).

| Column | Type | Description |
|---|---|---|
| `id` | integer PK | Auto-increment |
| `scope` | text NOT NULL | `global` or `project` |
| `project` | text | Repo slug for project-scoped rows; NULL for global rows |
| `key` | text NOT NULL | Setting key, e.g. `estimation` |
| `value` | jsonb NOT NULL | JSON value; merged at read time (project over global) |
| `updated_at` | timestamptz | Defaults to `now()`; updated on each write |

Unique constraint: `(scope, project, key)`.

Helpers: `get_setting(key, project=None)` and `set_setting(scope, key, value, project=None)` in `services/sprint_manager/settings_repo.py`.

Global seed row: `scope='global'`, `key='estimation'`, `value={"size_minutes":{"S":5,"M":15,"L":30,"XL":90},"buffer_pct":20,"thin_ac_buffer_pct":30}`. The `size_minutes` map is no longer redeclared here — `DEFAULT_ESTIMATION_CFG` imports the single canonical `SIZE_TO_MINUTES` from `services/sprint_manager/sizing.py` (issue #766). XL raised 60→90; size minutes now mean full-pipeline wall-clock (coder + tester, in-progress → UAT), not isolated agent effort.

### projects

Project registry synced from `apps/dashboard/projects.json` on server startup.

| Column | Type | Description |
|---|---|---|
| `id` | integer PK | Auto-increment |
| `repo` | text NOT NULL | Repo slug, e.g. `zealchaiwut/commander` (unique) |
| `name` | text NOT NULL | Human-readable project name |
| `created_at` | timestamptz | Defaults to `now()` |

### project_environments

Per-project clone environment paths (prd, uat, coder, tester). Cascade-deletes with `projects`.

| Column | Type | Description |
|---|---|---|
| `id` | integer PK | Auto-increment |
| `project_id` | integer FK → projects.id | Parent project (ON DELETE CASCADE) |
| `env` | text NOT NULL | Environment name, e.g. `prd`, `uat`, `coder`, `tester` |
| `local_directory` | text NOT NULL | Absolute path to the clone directory |
| `created_at` | timestamptz | Defaults to `now()` |

Index: `ix_project_environments_project_id` on `project_id`.
Unique constraint: `(project_id, env)`.

### project_todos (issue #843)

A lightweight, durable per-project to-do list that lives outside the ticket
backlog — a simple scratchpad scoped to each project. Deliberately **not**
ticket-like: no labels, assignees, or due dates. Portable column types
(Integer / Text / Boolean, ISO-8601 string timestamps) so the table applies
cleanly on SQLite as well as Postgres, matching `agent_runs`. When Neon is
disabled the dashboard serves todos from a local JSON store
(`.commander/project_todos_store.json`) via `services/sprint_manager/todo_repo.py`.

| Column | Type | Description |
|---|---|---|
| `id` | integer PK | Auto-increment |
| `project` | text NOT NULL | Owning project slug; every query is scoped by this |
| `text` | text NOT NULL | Free-text todo body |
| `done` | boolean NOT NULL | Completion flag; defaults to `false` |
| `position` | integer NOT NULL | Sort key; the list is always returned ascending by `position` |
| `created_at` | text NOT NULL | ISO-8601 UTC creation timestamp |
| `updated_at` | text NOT NULL | ISO-8601 UTC last-modified timestamp |
| `promoted_issue_number` | integer NULL | Reserved for a future planning bridge; never read, written, or exposed by the #843 API |

Index: `ix_project_todos_project_position` on `(project, position)`.

## Migrations

| Revision | Description |
|---|---|
| `a1b2c3d4e5f6` | Init (no-op baseline) |
| `b2c3d4e5f6a1` | Add `sprints` and `sprint_tickets` tables |
| `c3d4e5f6a1b2` | Add `projects` table, seeded from `projects.json` |
| `d4e5f6a1b2c3` | Add `project_environments` table, seeded from `projects.json` |
| `e5f6a1b2c3d4` | Add `actual_elapsed_seconds` (renamed) and `total_tokens` to `sprint_tickets` |
| `f6a7b8c9d0e1` | Add `events` table for structured log events |
| `g7h8i9j0k1l2` | Add `settings` KV table; add `estimated_size` to `sprint_tickets`; seed global estimation row |
| `h8i9j0k1l2m3` | Add `state` / `ended_at` / `end_reason` / `parent_label` to `sprints`; add `failed` to `sprint_status_enum`; add `sprint_ticket_order` table (issue #757) |
| `i9j0k1l2m3n4` | Add `agent_runs` table for per-agent duration tracking (portable Integer/Text columns; SQLite + Postgres) (issue #764) |
| `j0k1l2m3n4o5` | Add `risk_tier` and `model_used` columns to `agent_runs` (issue #790) |
| `k1l2m3n4o5p6` | Add `project_todos` table for per-project to-do lists (portable Integer/Text/Boolean columns; SQLite + Postgres) (issue #843) |

> **Neon is now an optional export target only (issue #758).** The dashboard and sprint manager run entirely off SQLite + local JSON; nothing writes to Neon in live paths. The Alembic migrations and SQLAlchemy models above remain so `scripts/export_to_neon.py` can push a snapshot on demand (`DATABASE_URL=… python scripts/export_to_neon.py`).

## SQLite Tables (local dashboard)

SQLite is the **authoritative, only live** store. As of sprint 57 it also holds the dashboard read model (`issues` mirror), ticket write-through state (`ticket_status`), and durable sprint lifecycle state (`sprints` + `sprint_ticket_order`). On first run from an empty DB the dashboard bootstraps a full GitHub sync to populate these tables (issue #760).

| Table | Description |
|---|---|
| `agents` | Active/recent Claude Code agent sessions |
| `events` | Streamed agent events (tool use, output, errors) |
| `token_usage` | Per-agent token consumption with `agent_role` and `model_name` columns |
| `project_events` | Structured audit log of project-level actions (settings changes, env path updates, etc.) |
| `ticket_status` | Write-through ticket state recorded by `state_machine.transition()` after a successful GitHub label edit (issue #755) |
| `issues` | Local mirror of repo issues kept fresh by ETag-conditional polling; the dashboard read-path serves from here, not GitHub (issue #756) |
| `sync_state` | Per-key ETags for `If-None-Match` conditional GitHub requests (issue #756) |
| `sprints` | Durable sprint lifecycle state — replaces the ephemeral `{label}-plan.json` / `{label}-pid` files as source of truth (issue #757) |
| `sprint_ticket_order` | Ticket execution order per sprint (`label`, `issue`, `position`) (issue #757) |
| `agent_runs` | One row per dispatched agent (coder, tester, …) with its own start/finish timestamps and wall-clock duration per issue (issue #764) |
| `sprint_history` | Append-only ledger of terminal sprint events (notably deletions) for the History feed; snapshots the ticket list before label-strip so deleted sprints stay visible (issue #805) |
| `advisor_suggestions` | Current draft suggestions from the most recent advisor run per project; replaced wholesale on each run (issue #881) |
| `advisor_look_ahead` | Ordered look-ahead entries (2–5 sprints) from the most recent advisor run per project; replaced wholesale on each run (issue #883) |

### ticket_status (issue #755)

Append-only write-through log; the latest row per issue is the current state. Lets the read-path skip a post-edit GitHub verify re-fetch.

| Column | Type | Description |
|---|---|---|
| `id` | integer PK | Auto-increment |
| `issue` | text NOT NULL | Issue number |
| `status` | text NOT NULL | State written after the label edit (e.g. `sit`, `uat`, `blocked`) |
| `actor` | text NOT NULL | Who wrote it (agent role / `dashboard`) |
| `note` | text | Optional note; nullable |
| `ts` | text NOT NULL | ISO 8601 timestamp |

Index: `(issue, ts DESC)`.

### issues (issue #756)

Mirror of repo issues. `labels` and `raw` hold JSON — `raw` is the full gh-CLI-shaped issue dict so readers reconstruct fields without a live call. Composite PK `(repo, issue_number)` allows multiple mirrored repos.

| Column | Type | Description |
|---|---|---|
| `repo` | text NOT NULL DEFAULT '' | Repo slug, e.g. `zealchaiwut/commander` |
| `issue_number` | integer NOT NULL | Issue number |
| `title` | text | Issue title |
| `state` | text | `open` / `closed` |
| `labels` | text NOT NULL DEFAULT '[]' | JSON array of labels |
| `updated_at` | text | GitHub `updated_at` |
| `raw` | text | Full gh-CLI-shaped issue JSON |

Index: `(repo, state)`.

### sprints / sprint_ticket_order (issue #757)

`sprints.state` is constrained to `planning` / `running` / `completed` / `cancelled` / `failed` (`failed` reserved for a future watchdog recovery sprint — no writer yet). The `{label}-plan.json` / `{label}-pid` files continue to be written as a deprecated cache until a later sprint removes them.

`sprints` columns: `label`, `project`, `state`, `created_at`, `started_at`, `ended_at`, `end_reason`, `parent_label`.
`sprint_ticket_order` columns: `label`, `issue`, `position` — PK `(label, issue)`, index `(label, position)`.

**Composite-key invariant — `(label, project)` (issues #1464, #1465).** Sprint `label` is unique only *within* a `project` — two projects may both own e.g. `sprint-66`. Every lifecycle read, write, and bulk-complete must scope to both `label` **and** `project`. `get_sprint_children(parent_label, project)` and `children_of(parent_label, ..., project)` filter by project so a lineage never pulls children from another project sharing the base label; the label-only path is a fallback that logs a warning when it fires. The target schema enforces `PRIMARY KEY (label, project)`; until that migration lands `project` acts as a soft scope on reads. **Historical incident (sprint-66):** before the key was enforced, `ON CONFLICT(label) DO UPDATE` let perf-coach's `sprint-66` overwrite commander's row and orphan its `66.x` children — repaired by `scripts/repair_sprint_collisions.py --apply` (restores the base row from plan.json / state.json / agent_runs without touching the surviving row); regression-guarded by `tests/test_sprint_collision_regression.py`. See `docs/architecture/sprint-lifecycle.md` § Composite-Key Invariant.

**Legacy-row backfill (issue #1460).** `_backfill_sprint_project()` (run inside `_create_sprint_lifecycle_tables`) fills empty `sprints.project` / `sprint_history.project` rows. Per label it resolves project via (1) `agent_runs.project` for a matching `sprint_label`, else (2) a disk walk for `.commander/sprints/<label>-plan.json` / `<label>-state.json` under each project root in `projects.json`, else (3) leaves it empty and logs a warning. Idempotent — only touches rows where `project = '' OR project IS NULL`. Also runnable as `scripts/backfill_sprint_project.py`.

**Run-artifact columns (issue #1160).** `sprints` also carries the ingested run artifact, written by `ingest_sprint_run_artifact()` at end-of-run and on lazy ingest: `issues_json`, `tokens`, `wall_clock_secs`, `reconciliation_json`, `summary_issue_url`, `summary_path`, `pr_number`, `post_sprint_json`, `estimate_accuracy`, `run_ingested_at`. The Sprint History / summary read path reads these from SQLite only — no disk fallback (issue #1161); on a cache miss it triggers a lazy ingest and returns the ingested row rather than reading raw disk (issue #1160).

**Denormalized summary counts (issues #1162, #1163).** `summary_settled_done`, `summary_uat_count`, `summary_failure_count` — all `INTEGER NOT NULL DEFAULT 0`. Materialized on run finish so the finish-card / history panes render counts without recomputation. The reconcile job repairs stale counts via `update_sprint_run_counts(label, issues_json, settled_done, uat_count, failure_count)`, which overwrites `issues_json` plus the three count columns in one write.

**DB is the sole source of truth for lifecycle state (sprint 74.2).** plan.json / labels / PID files are no longer read as state.

- **Read:** `sprint_state.current(label)` is the only sanctioned reader (issue #1091). It returns `canonical_lifecycle(db.get_sprint(label)["state"])` — zero disk reads, zero GitHub label lookups, zero fallback. Missing row → `"unknown"`. The History pane and `_derive_outcome_lifecycle` were migrated onto it (issues #1092, #1093); GET endpoints no longer mutate plan.json (issue #1096). See `docs/architecture/sprint-lifecycle.md`.
- **Write:** `db.transition_sprint_state(label, to_state, actor=...)` is the single guarded writer (issue #1087); `record_sprint_start/finish/needs_rework/ready_to_merge` route through it. It enforces the legal edge set (`draft→planned/running/...`, `running→ready_to_merge/needs_rework/deleted`, etc.) and rejects any `running→ready_to_merge|needs_rework|completed` transition unless `actor="manager"`. Returns a `TransitionResult(accepted, reason)`; never raises on a guard failure.
- **Reconciler:** the GitHub/orphan reconciler skips any `running` sprint whose owning manager PID is still alive (`.commander/sprints/<label>-pid`), and only settles a dead-PID running sprint to `ready_to_merge`/`needs_rework` based on ticket outcomes — all via `transition_sprint_state` (issues #1089, #1098).

Query with `GET /api/debug/token-usage/by-agent-model` for per-agent/model cost breakdown.

The `events` table also records dashboard activity events surfaced in the activity log: `ticket_label_changed` on every real label transition (issue #720), and scoped `sprint_started` / `sprint_finished` / `sprint_rerun` lifecycle events keyed to the target project (issue #721). Agent rows carry role + issue number so the activity log can link to the GitHub issue (issue #719).

### project_events

Audit log for project-level events recorded by `record_project_event()` in `apps/dashboard/db.py`.

| Column | Type | Description |
|---|---|---|
| `id` | integer PK | Auto-increment |
| `project` | text NOT NULL | Repo slug, e.g. `zealchaiwut/commander` |
| `created_at` | text NOT NULL | ISO 8601 UTC timestamp |
| `source` | text NOT NULL | Component that emitted the event, e.g. `settings_api` |
| `event_type` | text NOT NULL | Action type, e.g. `settings.update`, `env.update` |
| `target` | text | Entity the action targeted (key name, env name, etc.); nullable |
| `actor` | text | Who triggered the event (e.g. `dashboard`); nullable |
| `action_id` | text | Idempotency / correlation ID; nullable |
| `data` | text | JSON-encoded payload with before/after values or other context; nullable |

Indexes: `(project, created_at DESC)`, `(project, target)`, `(action_id)`.

### agent_runs (issue #764)

One row per dispatched agent per ticket, recorded at dispatch and finalized when
the agent finishes. Replaces ticket-level timing that only captured the whole
wall-clock span and lost per-agent resolution. Created identically on SQLite
(`_create_agent_runs_table` in `apps/dashboard/db.py`) and Postgres (Alembic
`0009_add_agent_runs`) using portable Integer/Text columns.

| Column | Type | Description |
|---|---|---|
| `id` | integer PK | Auto-increment |
| `issue_number` | integer NOT NULL | GitHub issue number |
| `sprint_label` | text NOT NULL | Sprint label, e.g. `sprint-59` |
| `agent` | text NOT NULL | Agent role (`coder`, `tester`, `documenter`, `reviewer`, `estimator`) |
| `started_at` | text NOT NULL | ISO 8601 dispatch timestamp |
| `finished_at` | text | ISO 8601 finish timestamp; nullable while running |
| `duration_seconds` | integer | Wall-clock seconds for the run; nullable while running |
| `outcome` | text | Run outcome; nullable |
| `total_tokens` | integer | Tokens consumed by this agent run; nullable |
| `risk_tier` | text | Risk classification before dispatch (`standard`, `elevated`, `critical`); nullable (issue #790) |
| `model_used` | text | Model selected for this run (e.g. `claude-haiku-4-5`, `claude-sonnet-4-6`); nullable (issue #790) |
| `routing_reason` | text | Human-readable explanation of model routing decision; nullable (issue #789/#790) |
| `worktree_sha` | text | Git SHA of worktree HEAD at dispatch; nullable (issue #788) |
| `base_sha` | text | Git SHA of the expected base branch at dispatch; nullable (issue #788) |
| `attempt_kind` | text | Dispatch type: `initial`, `redispatch`, `continuation`; nullable (issue #787) |
| `log_path` | text | Absolute path to the issue log file for this run; nullable (issue #783) |
| `provider` | text | LLM provider identifier for this run (e.g. `ICA`); set from the role's `CCPROXY_PROFILE` at dispatch; nullable (issue #1673) |

Indexes: `(issue_number, agent)`, `(sprint_label)`.

The `provider` column drives the **`caching: reduced`** indicator on the live
board (`project.html`) and run browser (`run_browser.html`): when an agent run's
provider is `ICA`, the proxy strips prompt-caching headers (full context is
re-sent each turn), so a yellow badge flags the higher token cost. It surfaces in
the live snapshot as `coder_provider` and in `/api/runs` as `provider`.

### sprint_history (issue #805)

Append-only ledger of terminal sprint events for the History feed
(`GET /api/sprints/history`). The `sprints` lifecycle table cannot represent a
`deleted` state — its CHECK constraint forbids it and the row is meant to be
gone once the label is stripped — so the delete path writes a self-contained
snapshot here **before** stripping labels, keeping deleted sprints visible.
Created by `_create_sprint_history_table` in `apps/dashboard/db.py`; written via
`record_sprint_history()` (best-effort, wrapped so a ledger write never blocks
the deletion) and read via `list_sprint_history()`.

| Column | Type | Description |
|---|---|---|
| `id` | integer PK | Auto-increment |
| `label` | text NOT NULL | Sprint label, e.g. `sprint-61` |
| `project` | text NOT NULL DEFAULT '' | Repo slug, e.g. `zealchaiwut/commander` |
| `lifecycle_state` | text NOT NULL | Terminal state, e.g. `deleted` |
| `end_reason` | text | Why the sprint ended; nullable |
| `duration` | integer | Wall-clock seconds; nullable |
| `tokens` | integer | Total tokens consumed; nullable |
| `estimate_accuracy` | real | Estimated vs actual accuracy ratio; nullable |
| `pr_number` | integer | Associated PR number; nullable |
| `summary_path` | text | Path to the sprint summary markdown; nullable |
| `issues_json` | text NOT NULL DEFAULT '[]' | JSON array of `{ticket_id, state, time_spent, pr_number}` captured at write time |
| `created_at` | text | ISO 8601 timestamp |

Index: `(created_at DESC)`.

### advisor_suggestions (issue #881)

Current draft suggestions from the most recent daily advisor run per project.
Replaced wholesale on every new run — no suggestion history beyond the current
set. Created by `_create_advisor_suggestions_table` in `apps/dashboard/db.py`.

| Column | Type | Description |
|---|---|---|
| `id` | integer PK | Auto-increment |
| `project` | text NOT NULL | Repo slug, e.g. `zealchaiwut/commander` |
| `run_at` | text NOT NULL | ISO 8601 UTC timestamp of the advisor run |
| `on_demand` | integer NOT NULL DEFAULT 0 | 1 if triggered via the on-demand API endpoint, 0 for scheduled |
| `pitch` | text NOT NULL | One-line suggestion title |
| `rationale` | text NOT NULL | Why the advisor recommends this suggestion |
| `milestone` | text NOT NULL | Target milestone label |
| `scope` | text NOT NULL | Effort size: `S`, `M`, or `L` |

Index: `ix_advisor_suggestions_project` on `(project)`.

Endpoints: `GET /api/projects/{project}/advisor/suggestions`, `POST /api/projects/{project}/advisor/run`, `POST /api/advisor/tick`.

### advisor_look_ahead (issue #883)

Ordered 2-to-5-sprint look-ahead entries from the most recent advisor run per project.
Replaced wholesale on every new run. Created by `_create_advisor_look_ahead_table`
in `apps/dashboard/db.py`.

| Column | Type | Description |
|---|---|---|
| `id` | integer PK | Auto-increment |
| `project` | text NOT NULL | Repo slug, e.g. `zealchaiwut/commander` |
| `run_at` | text NOT NULL | ISO 8601 UTC timestamp of the advisor run |
| `position` | integer NOT NULL | Sort order within the look-ahead list |
| `entry` | text NOT NULL | Look-ahead entry text |
| `on_demand` | integer NOT NULL DEFAULT 0 | 1 if triggered on-demand, 0 for scheduled |

Index: `ix_advisor_look_ahead_project` on `(project)`.

Endpoint: `GET /api/projects/{project}/advisor/look-ahead`.


## API Endpoints

### Sprints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sprints/{sprint_label}/finish-card` | Summary card data for a sprint. Always HTTP 200 — check `state` field (see below). |
| `GET` | `/api/sprints/{sprint_label}/timeline` | Per-issue Gantt timeline data for the running pane (issues #1146, #1147). Requires `project` query param. `sprint_label` must match `^sprint-\d+[a-z]?$` (else HTTP 400). |

**`GET /api/sprints/{sprint_label}/timeline` — response shape (issue #1146):**

Returns ordered per-issue segment data sourced from DB `agent_runs` (no render-time disk reads). GitHub labels are the issue-status truth; DB is the segment-time truth.

```
{
  "sprint_started_at": "<iso>",
  "server_now": "<iso>",
  "issues": [
    {
      "number": 519, "title": "...", "url": "...",
      "status": "done" | "running" | "queued",
      "size": "S"|"M"|"L"|"XL"|null,
      "calibrated_estimate": <minutes>,        // size→time mapping refined by Analytics actuals
      "estimated_remaining": <minutes>|null,   // running only = estimate − elapsed
      "segments": [ {"agent": "coder"|"tester", "start": "<iso>", "end": "<iso>"|null, "duration": <secs>} ]
    }
  ],
  "wrap_up_estimate": {"documenter": <minutes>, "reviewer": <minutes>?},  // reviewer only when reviewer_enabled
  "projected_finish": "<iso>"   // running remainder + queued estimates (per run mode) + wrap-up
}
```

`projected_finish` respects run mode: serial lays the queue out sequentially; pipeline overlaps coder/tester phases. The documenter/reviewer wrap-up is a single sprint-level object, never attached to individual issues.

**`GET /api/sprints/{sprint_label}/finish-card` — response states:**

| `state` value | When returned | Notes |
|---|---|---|
| `"running"` | Sprint is currently executing | Includes `in_flight_count`, `pending_count`, `done_count`, `wall_clock_secs`, `started_at` |
| `"completed"` / `"has_rework"` / `"cancelled"` | Sprint finished | Includes `done_count`, `failed_count`, `skipped_count`, `rework_count`, `wall_clock_secs`, `ended_at`, `summary_issue_url`, `summary_issue_num` |
| `"no_data"` | Sprint has never been run (no state file on disk) | HTTP 200 — do **not** expect 404; check `state` field instead |

> **Contract note (issue #671):** Before this change the endpoint returned HTTP 404 for the `no_data` case.
> It now always returns HTTP 200. Clients must check `state`, not the HTTP status code.
> See `docs/features/api.md` for the full response shape reference.

### Settings — LLM provider toggle (issue #1667)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/settings/provider` | Returns `{"provider": "anthropic"\|"ica"}` — the currently active backend (from the global `app_config.llmProvider` setting, default `anthropic`). |
| `POST` | `/api/settings/provider` | Switches the global LLM provider. Body `{"provider": "anthropic"\|"ica"}`. Validates the value (HTTP 400 if unknown), calls the claude-proxy profile endpoint, then persists `llmProvider`. Returns `{"provider": ..., "ok": true}`. |

**Mechanism.** `POST /api/settings/provider` calls `POST {COMMANDER_PROXY_URL}/profile` with `{"name": "<provider>"}` (default `COMMANDER_PROXY_URL=http://localhost:9090`) to instruct the local claude-proxy to activate the named profile. The new value is persisted to the global `app_config` settings key **only after** the proxy confirms. If the proxy is unreachable or returns non-2xx, the endpoint raises HTTP 503 and does **not** persist — no silent fallback. In-flight agent sessions keep their existing proxy connections; only newly dispatched agents inherit the changed profile. Service logic lives in `apps/dashboard/routers/llm_provider_service.py`.

`llmProvider` is a proxy-controlled field: `PUT /api/settings` rejects it with HTTP 422 (`_PROXY_CONTROLLED_FIELDS` in `settings_service.py`) so the value can only change through the proxy-aware endpoint above. It is registered in `KNOWN_FIELDS` (`services/sprint_manager/settings_schema.py`) as `{"secret": False, "default": "anthropic"}`.

The active provider at sprint start is captured on `SprintState.llm_provider` (read from `app_config.llmProvider`, falling back to `anthropic`), persisted in `state.json`, and exposed in the live snapshot as `llm_provider` so historical/running run views show the provider that was actually in use for that run rather than the current global setting (issue #1670).

### Sprint file maintenance (issue #735)

Archives stale per-sprint runtime files for a project's *finished* sprints into a reversible `.commander/sprints/archive/` subfolder. A sprint counts as finished only when it has a posted summary issue **or** a summary markdown **and** no live process is running it. Only `sprint-N-plan.json`, the zero-issue `sprint-N.json` placeholder, and `sprint-N-state.json` are moved; `sprint-N-status.json`, `sprint-N-estimate.json`, and summary markdown are never touched, and nothing is ever deleted. Idempotent. Also available as the CLI `python scripts/clean_sprint_files.py --project <id> [--dry-run]`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/maintenance/sprints/cleanup` | Body `{"project": "<id>", "dry_run": false}`. Archives finished-sprint runtime files; returns `{"archived": [...], "kept_count": N, "dry_run": bool}`. With `dry_run: true` returns the same shape without moving anything (UI preview before confirmation) |

### Calibration cache rebuild (issues #1332–#1334)

Clears `<project-root>/.commander/calibration_cache.json` and rescans every `sprint-*-state.json` under `.commander/sprints/` and `.commander/sprints/archive/`, resolving each completed ticket's size through a fallback chain (canonical estimate JSON → sprint-state estimate → `size-*` label, read from the state file or, failing that, the local SQLite `issues` mirror — no GitHub call, issue #1341). Idempotent — running twice on the same data yields the same counts. The cache also auto-refreshes when a sprint finishes (issue #1333). Canonical estimate JSON lives at `<project-root>/.commander/estimates/issue-<N>.json` (issue #1331). Also available as the CLI `python3 scripts/rebuild_calibration_cache.py --project <slug> [--dry-run]`. See `docs/features/estimation-lifecycle.md`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/maintenance/calibration/rebuild?project=<slug>` | Rebuild the calibration cache from full sprint history; returns a count summary (`processed_count` + `by_size_counts`) |

### Docs Scaffold

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/projects/{slug}/docs/scaffold/check` | Check which standard doc files are missing for a project; returns drift report |
| `POST` | `/api/projects/{slug}/docs/scaffold/apply` | Create any missing standard doc files from template; idempotent, never overwrites existing content |

### Analytics

| Method | Path | Description |
|---|---|---|
| `GET` | `/project/{slug}/analytics` | Retired standalone analytics page — permanent **301 redirect** to the in-chrome Analytics tab at `/project/{slug}/metrics` (issue #1846). The old `static/analytics.html` page is deleted |
| `GET` | `/api/sprint-progress` | Current sprint progress summary (tickets done/total, elapsed) |
| `GET` | `/api/projects/{slug}/analytics/metrics` | Aggregated sprint metrics: velocity, throughput, cycle time by size |
| `GET` | `/api/projects/{slug}/analytics/calibration` | Estimate accuracy data: estimated vs actual durations per size bucket |

Query params for calibration endpoint: `since` (ISO date), `until` (ISO date), `sprint` (label string) — all optional.

> **Note (issue #718):** Analytics metrics and calibration are sourced from local sprint state and estimate files under `.commander/`, not Neon. The analytics page works with the Neon kill switch enabled.

### Deploy (issues #722–#726)

Per-environment deploy/restart for the `prd` and `uat` environments. Each environment declares a `host` of `local` (launchd / stop-start scripts, pull-only `git pull --ff-only`) or `render` (Render API). Config persists under the `deploy_config` settings key (scope `project`).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/projects/{slug}/deploy-config` | Merged deploy config (seed defaults + stored overrides). Secret-safe: `render_api_key` is never returned in cleartext — render entries carry `render_api_key_set` (bool) and `render_api_key_masked` (e.g. `rnd_...cret`) instead |
| `PUT` | `/api/projects/{slug}/deploy-config` | Merge an incoming config per environment. A `render_api_key` that is omitted/null/empty leaves the stored secret unchanged; a non-empty value replaces it |
| `POST` | `/api/projects/{slug}/environments/{env}/deploy` | Trigger a deploy. `host=local` runs `git pull --ff-only origin <branch>` (no merge/push/checkout); `host=render` triggers a Render deploy |
| `POST` | `/api/projects/{slug}/environments/{env}/restart` | Restart the service. `host=local` uses `launchctl kickstart -k` (or configured stop/start scripts); restarting the dashboard's own process (`com.commander.dashboard`) routes through a detached helper so the 202 response flushes first. `host=render` calls the Render restart API |
| `GET` | `/api/projects/{slug}/environments/{env}/deploy-status` | Latest deploy status, normalized to `queued` / `building` / `live` / `failed`, plus commit SHA and last-deploy timestamp |
| `GET` | `/api/deploy/overview` | Secret-safe deploy cards aggregated across the known deploy projects (commander, perf-coach) for the Deploy tab |
| `POST` | `/api/projects/{slug}/environments/{env}/deploy-config/validate` | Validate an inline `working_dir` / `port` edit before persisting (issue #769). Port must be int 1–65535 and free on host; returns 400 with a user-visible message on failure, `200 {"ok": true}` when valid |
| `POST` | `/api/projects/{slug}/environments/{env}/stop` | Stop a local environment's service without destroying it (issue #771). `host=render` rejected with 400 |
| `POST` | `/api/projects/{slug}/environments/{env}/start` | Start a local environment's service without pulling code (issue #771). `host=render` rejected with 400 |
| `GET` | `/api/projects/{slug}/environments/{env}/run-state` | Live run state of a local environment — `running` / `stopped` / `idle` (issue #771). `host=local` only; `host=render` rejected with 400 (render run-state is derived client-side from deploy status) |

> The Deploy tab is scoped to the active project only (issue #768). Deploy cards also surface and inline-edit the run folder + port (issue #769), show a live capped log tail after deploy/restart/start (issue #770), and expose Start/Stop controls with a run-state badge alongside Deploy/Restart (issue #771). Headless `gh` auth for the launchd dashboard is wired via `GH_TOKEN` in the launchd plist + agent `.env` (issue #772).

### Run Browser (issue #783)

Forensic log viewer for all past sprint runs. All data served from local SQLite + disk log files — zero GitHub API calls.

| Method | Path | Description |
|---|---|---|
| `GET` | `/run-browser` | Serve the Run Browser HTML page; accepts `?sprint=<label>` deep-link query param |
| `GET` | `/runs` | List all past sprints with tickets and per-agent run metadata from `agent_runs` table |
| `GET` | `/runs/{sprint}/{issue}/{agent}/log` | Paginated log content. Query params: `page` (1-based), `limit` (lines per page, default 200) |
| `GET` | `/runs/{sprint}/{issue}/{agent}/log/tail` | Last N KB of a log file. Query param: `kb` (default 10) |

### Log Search (issue #785)

Cross-run full-text log search using ripgrep with DB-indexed pre-filtering.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/logs/search` | Search log files across all sprint runs. Query params: `project`, `sprint`, `issue` (int), `agent`, `event_type`, `level`, `time_range` (24h/7d/30d), `q` (substring). Returns up to 500 matches with `sprint`, `issue`, `agent`, `line_offset`, `text`, and `file` per match, plus `total`, `capped`, `timed_out`, and `query_ms` |
| `GET` | `/api/logs/runs/{sprint_label}/ticket-stats` | Per-ticket timing + token + failure detail for a Logs-tab run (issue #858). Optional `project` query param. One row per ticket carrying the coder/tester duration, the combined token total (any of which may be `null` → dash on the frontend), and the failure class + message for failed tickets. Aggregated locally from the durable `agent_runs` rows (`logs_stats_service.ticket_stats`); zero GitHub calls |

### Cost Analytics (issue #786)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/projects/{slug}/analytics/cost` | Token usage and cost breakdown by sprint, ticket, agent, and model. Sourced from local `token_usage` SQLite table; blended $/token rate applied |

### Env-var editor (issue #727)

Render-style `.env` editor for a project environment. Values are masked in the UI with per-row reveal; writes preserve original line order and inline comments for unchanged keys, rewrite changed keys in place, drop omitted keys, and append new keys.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/projects/{slug}/environments/{env}/env-vars` | Read the environment's `.env` as `[{"key", "value"}, ...]`; `[]` when the file does not exist |
| `PUT` | `/api/projects/{slug}/environments/{env}/env-vars` | Write the submitted key/value set back to the `.env` file (order/comment preserving) |

### Host doctor (issue #828)

Exposes the install-time host doctor (`scripts/doctor.py`) so the same PASS/FAIL
prerequisite checks the CLI prints can be triggered from the dashboard as a
button / status check. Single source of truth: the endpoint imports and runs the
script directly, so the button and the command never drift apart.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/doctor` | Run the pre-sprint host doctor and return its structured report. Shape: `{"ok": bool, "exit_code": int, "checks": [...], "failures": [...]}`. `ok`/`exit_code` reflect whether every check passed; each check carries its name, PASS/FAIL status, and remediation text |

### Debug — API call-volume & cache-hit observability (issue #1787)

In-process counters for tuning API traffic and cache effectiveness, backed by
`apps/dashboard/api_volume.py`. All counters are module-level integers that
reset on process restart — no persistence, no DB, no locks beyond the GIL.
Request paths are counted by a `server.py` HTTP middleware using the matched
route template (path params normalized, e.g. `/api/sprints/{label}/live`).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/debug/api-volume` | Self-documenting snapshot of in-process observability counters. Query param `n` (default 20) caps the number of top request paths returned. Returns `uptime_seconds`, `top_paths` (`[{path, count}]` sorted descending), `cache_stats` (`github_client` and `board_cache` hit/miss with computed `hit_rate`, plus `mirror_fallback` count of live `gh` calls made when the local issues mirror was absent), and `gh_subprocess_total` (every `gh` CLI invocation since start). Each block carries an inline `_doc` string |

### Sprint Workspace — Board / Running / History (issues #798–#810)

The Sprint tab is split into three sub-views (Board / Running / History). These
endpoints back the new panels; all are local-only (SQLite + cached tickets) and
add zero GitHub API calls.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sprints/history` | Paginated, enriched sprint-history feed for the History ledger (issue #805). Query params: `offset` (default 0), `limit` (default 20). Returns `{sprints: [...], offset, limit, total}`; each item carries `label`, `project`, `lifecycle_state`, `duration`, `tokens`, `estimate_accuracy`, `pr_number`, `summary_path`, `reconciliation`, and an `issues` list. Sources both lifecycle rows and `sprint_history` ledger rows; makes no GitHub calls. The `reconciliation` field (issue #856) carries the post-sprint loose-ends result `{all_clear, checks[], ...}` read verbatim from `<label>-state.json`, or `null` for sprints that closed before the feature was deployed |
| `GET` | `/api/sprints/{label}/run-stats` | Per-sprint `agent_runs` aggregation for the expanded History run-stats block — stat chips, coder/tester split bar, and gantt timeline segments (issue #810). Optional `project` query param |
| `GET` | `/api/sprints/{sprint_label}/preview-dag` | Read-only execution preview for a planned sprint (issue #809): predicted dispatch levels, file conflicts, cycles, and the unestimated-ticket list, computed by `dag_builder` over the sprint's cached tickets. Requires `project` query param; cached data only. Edges honour explicit ticket dependencies declared in issue bodies (issue #1404). Also returns `accuracy_warning` (issue #1417): set when the rolling estimator file-prediction accuracy under `.commander/estimates/accuracy/` is unreliable, so the preview can caution that the predicted file-conflict edges may be off |
| `GET` | `/api/sprints/{sprint_label}/dag-order-preview` | Compute a DAG-topological ticket order for a planned sprint without persisting it (issue #1420). Requires `project` query param; reuses `preview-dag` (cached data only, zero GitHub calls). Returns `new_order` (proposed ticket sequence — lower DAG levels first, within-level order preserved), `diff` (human-readable lines describing each ticket that moves earlier, with dep-edge hints), `is_noop` (true when the current plan already matches DAG order), and `partial` (true when the preview has unestimated tickets). Returns HTTP 409 when the sprint has circular dependencies |
| `GET` | `/scan-stale-branches` | List `feature/<N>-*` remote branches, map each to a sprint, and flag merged vs unmerged (issue #808). Query params: `repo` (required), `target` (optional base branch). Returns branches plus a `by_sprint` grouping |
| `POST` | `/cleanup-stale-branches` | Dry-run, then (with `confirm: true`) delete only merged stale branches — never unmerged (issue #808). Body `{repo, branches, target?, confirm}`; returns `{dry_run, target_branch, to_delete, deleted, skipped_unmerged, failed}` |
| `GET` | `/logs/tail` | Per-issue log tail for the Running-view node inspector (issue #804). Query params: `sprint`, `issue` (int), `project`, `tail_lines` (default, 1–2000). Keyed by sprint + issue (no agent segment); returns `{found, path, tail, mtime}` or `{found: false, candidate_paths, tail}` |

The live snapshot endpoint (`GET .../live`) is extended with the Running-view
metric strip keys derived from `agent_runs` (issue #803); the keys are present
only when their source exists so the frontend hides cards with no data. The Cost
Analytics response also gains a blended `usd_per_token` rate (`null` when no
price map is configured) so the capacity budget bar can derive a dollar forecast
(issue #801).

Two sprint-manager settings drive the new panels (`services/sprint_manager/settings_schema.py`):
`history_fold_size` (default 10) — most-recent sprints shown expanded in History
before older ones collapse into aggregate folds (issue #807); and
`sprint_budget_minutes` (default 180) — the sprint capacity budget that drives the
capacity bar (issue #801). A third setting, `reviewer_enabled` (default `false`,
issue #1146), gates the post-sprint reviewer wrap-up agent; when enabled the
timeline endpoint's `wrap_up_estimate` includes a `reviewer` minutes entry.

### Concurrent multi-coder dispatch (issues #1411–#1417)

The sprint runner can drive more than one coder at a time, each in an isolated
git worktree, instead of the prior serial loop. Three settings/config keys
govern lane capacity:

- `max_coder_slots` — warm worktree pool size (`settings_schema.py`, default 2,
  cap 4, issue #1411). Also readable from `sprint.yaml` as a top-level
  `max_coder_slots` key or `agent.coder.max_slots` (`config.py`, issue #1415);
  when unset there it falls back to the serial default of 1.
- `max_tester_slots` — `sprint.yaml` top-level key (`config.py`, issue #1415);
  unset → 1.

Resolution precedence at run start: per-run param > project settings (`cfg`) >
serial default (1). The resolved values are persisted on `SprintState`
(`state.py`, issue #1412): `max_coder_slots`, `max_tester_slots`, and
`active_coder_slots` (live count, updated by the scheduler). They are written
into the sprint status payload from the first post so the running pane shows
correct capacity immediately.

The **warm worktree pool** (`services/sprint_manager/worktree_pool.py`) lives at
`.commander/runtime/worktree-pool/slot-<i>/`, each slot a full worktree with its
own fresh virtualenv. Orphans from a prior crash are reconciled at sprint start;
the pool is torn down at sprint end. The **conflict-aware concurrent scheduler**
(`concurrent_scheduler.py`, issues #1412/#1413) runs role-flexible slots — each
slot executes a code task or a test task, preferring code and falling back to
tests, with file-overlap checks gating both; a tester rejection re-queues the
ticket to the front of the coder queue. With `max_coder_slots <= 1` it delegates
to the existing pipeline path (zero divergence). Concurrent merges are
serialized via the develop-merge guard, and `scripts/finish_feature.py` attempts
a single automated rebase on merge conflict before falling back to manual
(issue #1414).

The live snapshot endpoint (`GET .../live`) reports lane capacity
(`max_coder_slots` / `max_tester_slots` via `live_metrics.lane_capacity`) and now
lists **every active coder** as its own entry in `active_agents` (previously a
single coder slot), so the project page can render a multi-lane running view
(issue #1416).

**SSE-driven board (issue #1777).** The per-sprint stream
`GET /api/sprints/{label}/live/stream` (`routers/sprint_live.py`) now emits a
`snapshot` event carrying the full live-snapshot JSON — once on connect and then
every ~5 s (every 10 half-second ticks) — alongside the existing `log_line` and
`complete` events. This lets the sprint board and inspector bootstrap and refresh
metrics entirely from the stream, replacing the prior 2 s `/api/sprint-status` +
`/api/sprints/{label}/live` REST poll. On SSE disconnect the board falls back to
the sprint-103 `/api/running` slow poll (≥ 15 s) and resumes stream-driven updates
on reconnect. Steady-state outbound polling for a running sprint drops from
~60 calls/min to ≤ 4.

**SSE board invalidation (issue #1785).** `invalidate_board(project)`
(`routers/board_cache.py`) now, besides evicting the project's board-cache entry,
broadcasts a `{"type": "board_invalidated", "project": <slug>}` message over the
project SSE stream (via `logs_service.broadcast`; skipped cleanly when no asyncio
loop is running, e.g. sync test contexts). The issues-mirror sync loop
(`github_events_sync.py`) calls `invalidate_board(repo)` only when a mirror sync
returns HTTP 200 (rows actually changed) — a 304 no-change poll broadcasts
nothing. The project page handles the event by refetching the board, deferring the
refetch when the event arrives while the tab is hidden and flushing it on the next
`visibilitychange` back to visible.

**Estimator file-prediction accuracy (issue #1417).** On each merge,
`finish_feature.py` compares the ticket estimate's `files_likely_affected`
against the files the merge commit actually changed (`git diff-tree
--first-parent`) and writes a per-ticket precision/recall artifact to
`.commander/estimates/accuracy/issue-<N>.json`, updating a rolling `summary.json`
(`services/sprint_manager/estimate_accuracy.py`). All I/O is local — no GitHub or
network calls. `preview-dag` reads this rolling summary to set its
`accuracy_warning`.

### Daily Brief (issues #839–#842)

A per-project and home-roll-up "what happened / what's next" brief, assembled
from local sprint, ticket, and `agent_runs` data — zero GitHub API calls. Three
layers, each its own router module mounted under the `sprints` router (no route
lands in `server.py`, COMMANDER_GATE_MONOLITH, issue #761):

- **Assembly** (issue #839, `brief_service.py`) — pure structured assembly, kept
  LLM-free. An optional `date=YYYY-MM-DD` query param selects the window; default
  is today.
- **Summary** (issue #840, `brief_summary.py`) — a cached Haiku narrative over the
  assembled brief. Always falls back to a deterministic templated string, so the
  endpoints never 5xx. `regenerate` clears the cache and re-invokes the model.
- **Daily artifact** (issue #841, `brief_artifact.py`) — the full daily brief
  persisted per `(project, date)` so it is generated once and served instantly
  thereafter. The current day is lazily generated on first load; past dates are
  served from the store with a clear empty state when none was stored (never a
  recompute, never a 5xx). `regenerate` rebuilds, re-stores, and advances the
  timestamp.

The home page (issue #842, `static/home.html`) renders the home roll-up plus a
block per tracked project from these endpoints.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/projects/{slug}/brief` | Assembled structured brief for one project (optional `date`) |
| `GET` | `/api/brief` | Assembled home roll-up across all tracked projects (optional `date`) |
| `GET` | `/api/projects/{slug}/brief/summary` | Cached (or freshly generated) LLM summary for a project brief |
| `POST` | `/api/projects/{slug}/brief/summary/regenerate` | Clear the stored project summary and re-invoke the model |
| `GET` | `/api/brief/summary` | Cached (or freshly generated) one-line home recap |
| `POST` | `/api/brief/summary/regenerate` | Clear the stored home recap and re-invoke the model |
| `GET` | `/api/projects/{slug}/brief/daily` | Stored (or lazily generated) daily brief artifact for a project |
| `POST` | `/api/projects/{slug}/brief/daily/regenerate` | Rebuild and re-store the project daily brief, advancing the timestamp |
| `GET` | `/api/brief/daily` | Stored (or lazily generated) daily home roll-up artifact. Each entry in `brief.projects` is enriched (issue #1778) with `repo`, `name`, `icon`, `color`, `briefSummary`, and `milestone` so the home page renders in one request instead of 1+N |
| `POST` | `/api/brief/daily/regenerate` | Rebuild and re-store the home daily roll-up, advancing the timestamp |

### Project To-Dos (issue #843)

A lightweight, durable per-project to-do list backed by `project_todos` (Neon,
with a local JSON fallback when Neon is disabled). All routes are project-scoped:
a todo is only ever read, mutated, or deleted through its own project's path, so
one project's todos are never visible to another. The reserved
`promoted_issue_number` column is never read, written, or exposed. The routes
ride on the already-mounted `sprints` router so no route lands in `server.py`
(COMMANDER_GATE_MONOLITH, issue #761).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/projects/{project}/todos` | List all todos (active and done) for the project, ascending by `position` |
| `POST` | `/api/projects/{project}/todos` | Create a todo from body `{text}`; `done` defaults to `false`, `position` appends to end. Returns 201 with the new todo |
| `PATCH` | `/api/projects/{project}/todos/{todo_id}` | Update `done`, `text`, and/or `position` (body `{done?, text?, position?}`) — independently or together. 404 if the todo does not belong to the project |
| `DELETE` | `/api/projects/{project}/todos/{todo_id}` | Delete the todo; 204 on success, 404 if it does not belong to the project |
| `GET` | `/api/todos?projects=a,b,c` | Batch fetch (issue #1778): comma-separated slugs → slug-keyed `{slug: Todo[]}` map so the home page loads all projects' todos in one request. Unknown slugs are included with an empty list. Declared on a separate `batch_router` so the full `/api/todos` path is used without inheriting the `/api/projects` prefix |

The todo object shape is `{id, project, text, done, position, created_at, updated_at}` —
no ticket-like fields (labels, assignees, due dates) and no `promoted_issue_number`.

### Sprint label collision audit (issues #1461, #1464, #1465)

The `sprints` table keys rows by `label` alone (`label TEXT PRIMARY KEY`), so two
projects that reuse a sprint label compete for the same row — an `ON
CONFLICT(label) DO UPDATE` write from one project clobbers the other's row. This
sprint adds an audit/repair tool-chain plus project-scoped read paths to contain
that until a composite `(project, label)` key migration lands.

**Read-path scoping (#1464).** `db.get_sprint_children(parent_label, project=None)`
and `startup.children_of(parent_label, project_root=None, project=None)` now take an
optional `project` argument; when provided, child-sprint lookups add `AND project =
?` so they never match another project's rows. Callers in `startup.py`
(`_derive_outcome_lifecycle`, `_bulk_complete_unsettled_children`,
`_bulk_complete_assert_children_completed`, `_merge_steps_for_sprint_chain`,
`_sprint_merge_chain_pending`) thread the project through. The unscoped fallback
still works but logs a warning.

**Audit + repair scripts.**
`scripts/audit_sprint_collisions.py` is read-only: it cross-references `sprints`,
`sprint_history`, `agent_runs`, and per-clone `plan.json`/`state.json`, prints a
markdown collision table, and writes the `.commander/runtime/sprint-collisions.json`
manifest. `scripts/repair_sprint_collisions.py` (`--dry-run` / `--apply`) is a
surgical fix for the `sprint-66` incident — it restores commander's clobbered base
row from `plan.json → state.json → agent_runs` without touching perf-coach's row,
and asserts no stale running row survives.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/debug/sprint-collisions` | Return the `.commander/runtime/sprint-collisions.json` manifest written by `audit_sprint_collisions.py`. Returns `[]` when the manifest has not been generated yet (or is unreadable). Read-only; reads from disk, no DB query |

### Read-only docs endpoints (issue #1859)

Read-only access to a project's markdown docs from disk — no DB, no GitHub. Backed by `routers/docs.py` + `routers/docs_service.py`. The clone root is resolved from the project slug via `docs_service.resolve_clone_root(slug)`, which honours both nested (`~/dev/<slug>/main/`) and flat (`~/dev/<slug>/`) layouts; `COMMANDER_PROJECTS_BASE` overrides the `~/dev` base.

The **allowed doc set** is `docs/**/*.md` plus the root allowlist `{README.md, CHANGELOG.md}`. `get_doc` enforces, in order: reject absolute paths (400); reject non-`.md` extensions (400); reject any `..` path segment (400); resolve and verify containment inside the clone root (400 on escape); verify membership in the allowed set (400); reject symlinks (400); 404 if the file is missing. Listing skips symlinks.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/projects/{slug}/docs` | List allowed `.md` docs as `[{path, size, mtime}]` (docs/** then README/CHANGELOG). 404 if the slug is not a tracked project |
| `GET` | `/api/projects/{slug}/docs/{path}` | Fetch one allowed `.md` file as `{path, content}`. 400 on path-traversal/extension/symlink violations, 404 if absent |

### Agent operate guide (issue #1861)

Serves the canonical agent operate guide (`docs/agent-guide.md`, 5 canonical workflow recipes) as JSON. Backed by `routers/agent_guide.py` + `routers/agent_guide_service.py`. `GUIDE_PATH` is exposed at module level for test monkeypatching.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/agent-guide` | Return `{content, version}` for `docs/agent-guide.md` — `content` is the full markdown, `version` is a 16-hex-char SHA-256 fingerprint of the content (stable across unchanged reads). 404 with a helpful message if the file is absent |
