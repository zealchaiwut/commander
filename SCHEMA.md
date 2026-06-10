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

`sprints` columns: `label` (PK), `project`, `state`, `created_at`, `started_at`, `ended_at`, `end_reason`, `parent_label`.
`sprint_ticket_order` columns: `label`, `issue`, `position` — PK `(label, issue)`, index `(label, position)`.

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

Indexes: `(issue_number, agent)`, `(sprint_label)`.

## API Endpoints

### Sprints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sprints/{sprint_label}/finish-card` | Summary card data for a sprint. Always HTTP 200 — check `state` field (see below). |

**`GET /api/sprints/{sprint_label}/finish-card` — response states:**

| `state` value | When returned | Notes |
|---|---|---|
| `"running"` | Sprint is currently executing | Includes `in_flight_count`, `pending_count`, `done_count`, `wall_clock_secs`, `started_at` |
| `"completed"` / `"has_rework"` / `"cancelled"` | Sprint finished | Includes `done_count`, `failed_count`, `skipped_count`, `rework_count`, `wall_clock_secs`, `ended_at`, `summary_issue_url`, `summary_issue_num` |
| `"no_data"` | Sprint has never been run (no state file on disk) | HTTP 200 — do **not** expect 404; check `state` field instead |

> **Contract note (issue #671):** Before this change the endpoint returned HTTP 404 for the `no_data` case.
> It now always returns HTTP 200. Clients must check `state`, not the HTTP status code.
> See `docs/features/api.md` for the full response shape reference.

### Sprint file maintenance (issue #735)

Archives stale per-sprint runtime files for a project's *finished* sprints into a reversible `.commander/sprints/archive/` subfolder. A sprint counts as finished only when it has a posted summary issue **or** a summary markdown **and** no live process is running it. Only `sprint-N-plan.json`, the zero-issue `sprint-N.json` placeholder, and `sprint-N-state.json` are moved; `sprint-N-status.json`, `sprint-N-estimate.json`, and summary markdown are never touched, and nothing is ever deleted. Idempotent. Also available as the CLI `python scripts/clean_sprint_files.py --project <id> [--dry-run]`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/maintenance/sprints/cleanup` | Body `{"project": "<id>", "dry_run": false}`. Archives finished-sprint runtime files; returns `{"archived": [...], "kept_count": N, "dry_run": bool}`. With `dry_run: true` returns the same shape without moving anything (UI preview before confirmation) |

### Docs Scaffold

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/projects/{slug}/docs/scaffold/check` | Check which standard doc files are missing for a project; returns drift report |
| `POST` | `/api/projects/{slug}/docs/scaffold/apply` | Create any missing standard doc files from template; idempotent, never overwrites existing content |

### Analytics

| Method | Path | Description |
|---|---|---|
| `GET` | `/project/{slug}/analytics` | Serve the analytics HTML page for a project |
| `GET` | `/api/sprint-progress` | Current sprint progress summary (tickets done/total, elapsed) |
| `GET` | `/api/projects/{slug}/analytics/metrics` | Aggregated sprint metrics: velocity, throughput, cycle time by size |
| `GET` | `/api/projects/{slug}/analytics/calibration` | Estimate accuracy data: estimated vs actual durations per size bucket |

Query params for calibration endpoint: `since` (ISO date), `until` (ISO date), `sprint` (label string) — all optional.

> **Note (issue #718):** Analytics metrics and calibration are sourced from local sprint state and estimate files under `.commander/`, not Neon. The analytics page works with the Neon kill switch enabled.

### Notes

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/notes` | Return the global notes body (`{"body": "..."}`); empty string if none saved yet |
| `PUT` | `/api/notes` | Persist the full global notes body (`{"body": "..."}`) to `.commander/notes.json` |

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
