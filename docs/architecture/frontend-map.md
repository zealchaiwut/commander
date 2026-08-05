# Frontend Map — `project.html` Views and API Bindings

> **Status:** companion to [`boundaries.md`](./boundaries.md). Documentation only
> (issue #793) — no frontend behavior or API call is changed by the ticket that
> created it.

`apps/dashboard/static/project.html` (~24k lines) is the single-page project view.
Navigation is client-side: a top nav switches top-level **tabs** via
`switchTab('<id>')`, and several tabs have their own sub-views. This file maps
every view to the API endpoints it calls, so the router extraction (issue #761)
can confirm no view loses its backend when an endpoint moves modules.

## Sitemap

Top-level tabs (each is a `switchTab('<id>')` target):

- **`sprint-mgmt`** — Sprint Management (the default landing tab)
  - sub-view `board` — sprint board / kanban columns
  - sub-view `history` — finished-sprint history
- **`tickets`** — single ticket create/draft
- **`bulk-create`** — bulk ticket draft → review → post pipeline
- **`failures`** — Failures inbox: normalized view of failed tickets/agents (added #2019/#2020)
- **`brain`** — Brain: FTS5 doc search + pre-built panels (added #2028)
- **`deploy`** — Deploy & environments
- **`settings`** — Settings & sync

> **Deleted tabs (removed 2026-07-30 by #2024/#2025):** `logs` (Logs & Activity)
> and `metrics` (Metrics / Analytics) were removed from the top nav; their backend
> routes remain in `routers/logs.py`, `routers/analytics.py` etc. and redirect
> legacy deep-links to the Failures inbox (see `routers/pages.py`).

Modals / overlays (not tabs, but distinct views):

- **Add Project modal** (`apmSwitchTab`): `init`, `add`
- **Live View** — sprint live stream panel (opened from the board)

Page shell (loaded on every view, before any tab is shown): version banner,
GitHub-auth indicator, and the project header.

## Page → API Binding

Every view above, with the endpoints it calls. Paths use `:var` for path params.

### Page shell (all views)

| View | API calls |
|------|-----------|
| Shell / header | `GET /api/health`, `GET /api/version`, `GET /api/home`, `GET /api/project-details`, `GET /api/gh-auth-status`, `GET /api/github/labels` |

### `sprint-mgmt`

| View | API calls |
|------|-----------|
| `sprint-mgmt` (board) | `GET /api/sprint-management/issues`, `GET /api/sprint-nav-status`, `GET /api/sprint-nav-summary`, `GET /api/sprint-progress`, `GET /api/sprints/running-all`, `GET /api/sprints/:label/branch-status`, `GET /api/sprints/:label/finish-card`, `POST /api/sprints/run`, `POST /api/sprints/create`, `POST /api/sprints/batch-labels`, `POST /api/sprints/:label/rename`, `DELETE /api/sprints/run/:label` |
| `sprint-mgmt` › `board` | `GET /api/sprints/running-all`, `GET /api/sprint-management/issues` |
| `sprint-mgmt` › `history` | `GET /api/sprint-history`, `GET /api/sprints/summaries`, `GET /api/sprints/timeline`, `POST /api/sprints/cleanup-empty`, `POST /api/maintenance/sprints/cleanup` |

### `tickets`

| View | API calls |
|------|-----------|
| `tickets` | `POST /api/tickets/draft`, `POST /api/tickets/create`, `POST /api/issues/:id/sprint-label`, `POST /api/issues/:id/close` |

### `bulk-create`

| View | API calls |
|------|-----------|
| `bulk-create` | `POST /api/tickets/bulk`, `GET /api/tickets/bulk/:job_id`, `GET /api/tickets/bulk/:job_id/stream` (SSE), `POST /api/tickets/bulk/:job_id/estimate-draft`, `POST /api/tickets/bulk/:job_id/redraft`, `POST /api/tickets/bulk/:job_id/retry`, `POST /api/tickets/bulk/:job_id/retry-all`, `POST /api/tickets/bulk/:job_id/retry-with-body`, `POST /api/tickets/bulk/:job_id/retry-with-image`, `POST /api/tickets/bulk/:job_id/skip`, `POST /api/tickets/bulk/:job_id/stop`, `POST /api/tickets/bulk/:job_id/post-selected`, `POST /api/tickets/bulk/:job_id/size-remedy-comment`, `POST /api/tickets/bulk/:job_id/size-remedy-images`, `DELETE /api/tickets/bulk/:job_id` |

### `failures`

| View | API calls |
|------|-----------|
| `failures` | `GET /api/failures?project=:slug[&since=…][&category=…]` |

### `brain`

| View | API calls |
|------|-----------|
| `brain` | `GET /api/brain/search?q=:query[&project=:slug]`, `GET /api/brain/panels?project=:slug` |

### `deploy`

| View | API calls |
|------|-----------|
| `deploy` | `GET /api/deploy/overview`, `GET /api/projects/:slug/environments`, `GET /api/projects/:slug/environments/:env/run-state`, `GET /api/projects/:slug/environments/:env/deploy-status`, `POST /api/projects/:slug/environments/:env/deploy`, `POST /api/projects/:slug/environments/:env/restart`, `POST /api/projects/:slug/environments/:env/stop`, `POST /api/projects/:slug/environments/:env/start`, `POST /api/deploy/promote` |

### `settings`

| View | API calls |
|------|-----------|
| `settings` | `GET /api/settings`, `PUT /api/settings`, `GET /api/projects/:slug/settings`, `PUT /api/projects/:slug/settings`, `GET /api/settings/sync/status`, `POST /api/settings/sync/diff`, `POST /api/settings/sync/commit`, `GET /api/fs/list`, `GET /api/projects/notes`, `POST /api/projects/notes` |

### Modals

| View | API calls |
|------|-----------|
| Add Project modal › `init` | `POST /api/projects/init` |
| Add Project modal › `add` | `GET /api/projects`, `POST /api/projects` |
## Notes

- SSE streams (`/api/tickets/bulk/:job_id/stream`, `/api/sprints/:label/live/stream`)
  use `EventSource`; when their endpoints move modules the event name and payload
  shape must be preserved (see `static/AGENTS.md` danger zones).
- The page shell endpoints are cross-cutting and map to the `system/health` and
  `logs/activity` clusters in [`boundaries.md`](./boundaries.md).
