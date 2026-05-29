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
| `elapsed_seconds` | integer | Total wall-clock seconds for completed ticket |

Index: `ix_sprint_tickets_sprint_position` on `(sprint_id, position)`.
Unique constraint: `(sprint_id, issue_number)`.

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

## SQLite Tables (local dashboard)

| Table | Description |
|---|---|
| `agents` | Active/recent Claude Code agent sessions |
| `events` | Streamed agent events (tool use, output, errors) |
| `token_usage` | Per-agent token consumption with `agent_role` and `model_name` columns |

Query with `GET /api/debug/token-usage/by-agent-model` for per-agent/model cost breakdown.
