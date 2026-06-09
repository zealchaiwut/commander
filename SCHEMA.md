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

Global seed row: `scope='global'`, `key='estimation'`, `value={"size_minutes":{"S":5,"M":15,"L":30,"XL":60},"buffer_pct":20,"thin_ac_buffer_pct":30}`.

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

## SQLite Tables (local dashboard)

| Table | Description |
|---|---|
| `agents` | Active/recent Claude Code agent sessions |
| `events` | Streamed agent events (tool use, output, errors) |
| `token_usage` | Per-agent token consumption with `agent_role` and `model_name` columns |
| `project_events` | Structured audit log of project-level actions (settings changes, env path updates, etc.) |

Query with `GET /api/debug/token-usage/by-agent-model` for per-agent/model cost breakdown.

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

## API Endpoints

### Analytics

| Method | Path | Description |
|---|---|---|
| `GET` | `/project/{slug}/analytics` | Serve the analytics HTML page for a project |
| `GET` | `/api/sprint-progress` | Current sprint progress summary (tickets done/total, elapsed) |
| `GET` | `/api/projects/{slug}/analytics/metrics` | Aggregated sprint metrics: velocity, throughput, cycle time by size |
| `GET` | `/api/projects/{slug}/analytics/calibration` | Estimate accuracy data: estimated vs actual durations per size bucket |

Query params for calibration endpoint: `since` (ISO date), `until` (ISO date), `sprint` (label string) — all optional.
