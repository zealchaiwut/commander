# Architecture Boundary Contract — Routers, Services, Repos

> **Status:** canonical boundary map for the `server.py` strangler-fig extraction (issue #761).
> This document is the contract every future extraction ticket references and consumes.
> It is **documentation only** — no code is moved by the ticket that created it (#793).

## What This Is

`apps/dashboard/server.py` is a ~13k-line FastAPI monolith. Extracting modules
without a target architecture map risks producing a *different* monolith. This file
inventories **every** current endpoint in `server.py`, assigns each to exactly one of
eight proposed router clusters, and states the layer rules and extraction order so
each future extraction ticket has an unambiguous, self-contained checklist.

**Total endpoints inventoried:** 166 (matches `grep -cE '^@app\.' server.py`).

Already-extracted routers (mounted in `server.py` via `app.include_router`):
`analytics_router`, `backup_router`, `log_search_router`, `runs_router`. Their target
clusters below note the partial extraction; remaining endpoints still live in the monolith.

## Layer Rules

These three rules are non-negotiable and apply to every cluster:

1. **Routers = HTTP only.** A router module declares `APIRouter(...)`, parses the
   request, calls exactly one service function, and shapes the HTTP response
   (status codes, Pydantic v2 models, SSE framing). No business logic, no SQL, no
   `gh`/GitHub calls inline in a handler.
2. **Services = logic, no FastAPI imports.** A service module holds all business
   logic and orchestration. It must not import `fastapi`, `Request`, `Response`, or
   any HTTP concept. It is pure Python callable from tests without an app context.
3. **Repos = only SQL / GitHub callers.** A repo module is the only place that issues
   SQL (via `db.py`) or talks to the GitHub API (via `github_client.py`) or touches
   the filesystem/JSON state stores. Services call repos; repos never call services.

Dependency direction is strictly one-way: `router → service → repo`. A repo never
imports a service; a service never imports a router.

## Cluster Summary

| Cluster | Target router module | Service module | Repo / data layer | Endpoints |
|---------|----------------------|----------------|-------------------|-----------|
| `sprints` | `apps/dashboard/routers/sprints.py` | `apps/dashboard/routers/sprints_service.py` | services/sprint_manager/sprint_repo.py (Neon mirror) + `.commander/sprints/*` state JSON + GitHub labels via github_client.py | 33 |
| `tickets/issues` | `apps/dashboard/routers/tickets.py` | `apps/dashboard/routers/tickets_service.py` | github_client.py (GitHub Issues API, 30s cache) + in-memory/`.commander/bulk-jobs/*.json` bulk-job store | 26 |
| `projects` | `apps/dashboard/routers/projects.py` | `apps/dashboard/routers/projects_service.py` | apps/dashboard/projects.py (project registry) + per-project deploy/run-state JSON + filesystem (clones) | 18 |
| `settings` | `apps/dashboard/routers/settings.py` | `apps/dashboard/routers/settings_service.py` | settings JSON + per-project `.env` / deploy-config files + notes store + filesystem listing | 21 |
| `analytics` | `apps/dashboard/routers/analytics.py  (already mounted via analytics_router)` | `apps/dashboard/routers/analytics_service.py` | token_usage table (SQLite) + `.commander/estimates/*.json` + mis-sizing history JSON | 22 |
| `backup` | `apps/dashboard/routers/backup.py  (already mounted via backup_router)` | `apps/dashboard/routers/backup_service.py` | git (branch/stale-branch ops) + settings-sync (git commit) + filesystem snapshots | 9 |
| `logs/activity` | `apps/dashboard/routers/logs.py  (runs_router + log_search_router already mounted)` | `apps/dashboard/routers/logs_service.py` | events + agents + token_usage tables (SQLite) + dispatch-log files + SSE streams | 14 |
| `system/health` | `apps/dashboard/routers/system.py` | `apps/dashboard/routers/system_service.py` | config.py / env vars + github_client.py (auth status) + static files on disk | 23 |
| **Total** | | | | **166** |

## Extraction Order

Lowest-risk cluster first (read-only, fewest cross-dependencies); highest-risk last
(stateful, most interdependencies). Already-extracted routers are noted but kept in
sequence so the contract stays complete.

1. **`system/health`** — smallest / lowest-risk — mostly read-only page shells, health, version, static files; no shared mutable state
2. **`settings`** — isolated CRUD over JSON / `.env` files; no sprint or GitHub write coupling
3. **`logs/activity`** — read-heavy; runs + log-search already extracted, so the pattern is proven
4. **`analytics`** — read-only metrics; analytics_router already extracted — finish the remaining endpoints
5. **`backup`** — reference extraction — already a standalone router; formalize service/repo split
6. **`projects`** — more side effects (deploy/env operations, filesystem) but well-scoped per project
7. **`tickets/issues`** — GitHub writes + stateful bulk-job pipeline; higher blast radius
8. **`sprints`** — largest and most interdependent (run/rerun/state/preflight) — extract last

## Clusters

### Cluster: `sprints`

Sprint lifecycle: list, run, rerun, state, preflight, dependency ordering, rename/delete. The largest and most interdependent cluster — extract last.

- **Target router module:** `apps/dashboard/routers/sprints.py`
- **Service module:** `apps/dashboard/routers/sprints_service.py`
- **Repo/data layer:** services/sprint_manager/sprint_repo.py (Neon mirror) + `.commander/sprints/*` state JSON + GitHub labels via github_client.py

**Per-endpoint checklist (33 endpoints):**

- [ ] `GET /api/sprints` (server.py:1571) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprint-nav-status` (server.py:1583) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprint-progress` (server.py:1675) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprint-nav-summary` (server.py:1780) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprint-status` (server.py:3818) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprint-status` (server.py:3843) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprint-summary` (server.py:3871) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprint-history` (server.py:4296) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprint-history-content` (server.py:4358) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/batch-labels` (server.py:4705) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprint-run` (server.py:4874) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprint-management/issues` (server.py:5471) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/timeline` (server.py:5633) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/summaries` (server.py:5717) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/{sprint_label}/preflight-fix` (server.py:5957) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/cycle-check` (server.py:6074) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/conflicts` (server.py:6109) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/dep-order` (server.py:6173) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/preflight` (server.py:6289) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/run` (server.py:6412) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/running-all` (server.py:6674) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/state` (server.py:6688) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `DELETE /api/sprints/run/{sprint_label}` (server.py:6721) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/create` (server.py:7475) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/{sprint_label}/rename` (server.py:7539) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/{sprint_label}/tickets/reorder` (server.py:7613) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/{sprint_label}/plan` (server.py:7642) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/state` (server.py:7952) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/branch-status` (server.py:9335) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/rerun/preview` (server.py:9414) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/rerun-preview` (server.py:9464) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/{sprint_label}/rerun` (server.py:9507) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only
- [ ] `DELETE /api/sprints/{sprint_label}` (server.py:9724) — move to `sprints.py`, delegate logic to service, keep handler HTTP-only

### Cluster: `tickets/issues`

Issue CRUD, approve/reject/close, sprint-label assignment, and the bulk-create draft/post pipeline.

- **Target router module:** `apps/dashboard/routers/tickets.py`
- **Service module:** `apps/dashboard/routers/tickets_service.py`
- **Repo/data layer:** github_client.py (GitHub Issues API, 30s cache) + in-memory/`.commander/bulk-jobs/*.json` bulk-job store

**Per-endpoint checklist (26 endpoints):**

- [ ] `GET /api/issues` (server.py:1797) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/issues/{issue_id}/approve` (server.py:1809) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/{issue_id}/approve` (server.py:1820) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/issues/{issue_id}/reject` (server.py:1833) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/issues/{issue_id}/close` (server.py:1844) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/issues/{issue_id}/test-report` (server.py:1855) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprint-planning/issues` (server.py:4473) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprint-planning/assign` (server.py:4517) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/open-issues` (server.py:4598) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/issues/{issue_id}/sprint-label` (server.py:4626) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/draft` (server.py:10642) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/create` (server.py:11186) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/bulk` (server.py:11782) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/tickets/bulk/{job_id}` (server.py:11965) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/tickets/bulk/{job_id}/stream` (server.py:11995) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/bulk/{job_id}/stop` (server.py:12054) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `DELETE /api/tickets/bulk/{job_id}` (server.py:12065) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/bulk/{job_id}/skip` (server.py:12130) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/bulk/{job_id}/retry` (server.py:12163) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/bulk/{job_id}/redraft` (server.py:12227) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/bulk/{job_id}/post-selected` (server.py:12362) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/bulk/{job_id}/retry-with-body` (server.py:12634) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/bulk/{job_id}/retry-with-image` (server.py:12654) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/bulk/{job_id}/retry-all` (server.py:12720) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/bulk/{job_id}/size-remedy-comment` (server.py:12755) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/bulk/{job_id}/size-remedy-images` (server.py:12906) — move to `tickets.py`, delegate logic to service, keep handler HTTP-only

### Cluster: `projects`

Project registration, environment deploy/restart/stop/start, run-state, and per-project finish operations.

- **Target router module:** `apps/dashboard/routers/projects.py`
- **Service module:** `apps/dashboard/routers/projects_service.py`
- **Repo/data layer:** apps/dashboard/projects.py (project registry) + per-project deploy/run-state JSON + filesystem (clones)

**Per-endpoint checklist (18 endpoints):**

- [ ] `GET /api/projects` (server.py:1928) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/projects` (server.py:1939) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `DELETE /api/projects/{owner}/{repo_name}` (server.py:1977) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/{project}/running-sprint` (server.py:2030) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/projects/{slug}/environments/{env}/deploy` (server.py:2565) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/projects/{slug}/environments/{env}/restart` (server.py:2625) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/projects/{slug}/environments/{env}/stop` (server.py:2735) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/projects/{slug}/environments/{env}/start` (server.py:2758) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/{slug}/environments/{env}/run-state` (server.py:2781) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/{slug}/environments/{env}/deploy-status` (server.py:2811) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/deploy/overview` (server.py:2850) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/{slug}/environments` (server.py:3068) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `PUT /api/projects/{slug}/environments` (server.py:3095) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/projects/{owner}/{repo_name}/approve-batch` (server.py:3462) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/projects/init` (server.py:3478) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/project-details` (server.py:3559) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/{owner}/{repo_name}/sprints/{label}/finish-preview` (server.py:9792) — move to `projects.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/projects/{owner}/{repo_name}/sprints/{label}/finish` (server.py:9886) — move to `projects.py`, delegate logic to service, keep handler HTTP-only

### Cluster: `settings`

Global and per-project settings, deploy-config, env-vars, sprint goal/order, docs scaffold, and notes.

- **Target router module:** `apps/dashboard/routers/settings.py`
- **Service module:** `apps/dashboard/routers/settings_service.py`
- **Repo/data layer:** settings JSON + per-project `.env` / deploy-config files + notes store + filesystem listing

**Per-endpoint checklist (21 endpoints):**

- [ ] `DELETE /api/projects/{slug}/settings` (server.py:1962) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/settings` (server.py:2264) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `PUT /api/settings` (server.py:2275) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/{slug}/settings` (server.py:2289) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `PUT /api/projects/{slug}/settings` (server.py:2301) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/{slug}/deploy-config` (server.py:2380) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `PUT /api/projects/{slug}/deploy-config` (server.py:2393) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/projects/{slug}/environments/{env}/deploy-config/validate` (server.py:2409) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/fs/list` (server.py:2973) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/{slug}/environments/{env}/env-vars` (server.py:3174) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `PUT /api/projects/{slug}/environments/{env}/env-vars` (server.py:3206) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/{slug}/docs/scaffold/check` (server.py:3271) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/projects/{slug}/docs/scaffold/apply` (server.py:3298) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/goal` (server.py:5449) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/goal` (server.py:5459) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/order` (server.py:5859) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/order` (server.py:5871) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/notes` (server.py:13340) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/projects/notes` (server.py:13355) — move to `settings.py`, delegate logic to service, keep handler HTTP-only
### Cluster: `analytics`

Metrics, calibration, estimate-vs-actual, outcome/finish-card, and mis-sizing analytics. Partially extracted already (analytics_router).

- **Target router module:** `apps/dashboard/routers/analytics.py  (already mounted via analytics_router)`
- **Service module:** `apps/dashboard/routers/analytics_service.py`
- **Repo/data layer:** token_usage table (SQLite) + `.commander/estimates/*.json` + mis-sizing history JSON

**Per-endpoint checklist (22 endpoints):**

- [ ] `GET /api/estimator/health` (server.py:1865) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/issues/{issue_id}/estimate` (server.py:1873) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/plan-usage` (server.py:3604) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/estimate-summary` (server.py:5900) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/estimate` (server.py:7830) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/estimates/batch` (server.py:7863) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/outcome` (server.py:8050) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/estimate-vs-actual` (server.py:8257) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/calibration` (server.py:8384) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/metrics/sprints` (server.py:8525) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/{slug}/analytics/metrics` (server.py:8959) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/{slug}/analytics/calibration` (server.py:9098) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/reports/daily` (server.py:9116) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/finish-card` (server.py:9164) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/tickets/bulk/{job_id}/estimate-draft` (server.py:12097) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/mis-sizing-flags` (server.py:13109) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/{sprint_label}/mis-sizing-flags/generate` (server.py:13125) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/{sprint_label}/mis-sizing-flags/{issue_id}/action` (server.py:13144) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/mis-sizing/history` (server.py:13200) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/mis-sizing/rebuild` (server.py:13210) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/mis-sizing/config` (server.py:13303) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/mis-sizing/config` (server.py:13313) — move to `analytics.py`, delegate logic to service, keep handler HTTP-only

### Cluster: `backup`

Backup/ops/maintenance: settings sync, stale-branch cleanup, empty-sprint cleanup, deploy promotion. The reference extraction — already a standalone router.

- **Target router module:** `apps/dashboard/routers/backup.py  (already mounted via backup_router)`
- **Service module:** `apps/dashboard/routers/backup_service.py`
- **Repo/data layer:** git (branch/stale-branch ops) + settings-sync (git commit) + filesystem snapshots

**Per-endpoint checklist (9 endpoints):**

- [ ] `GET /api/settings/sync/status` (server.py:2890) — move to `backup.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/settings/sync/diff` (server.py:2904) — move to `backup.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/settings/sync/commit` (server.py:2936) — move to `backup.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/maintenance/sprints/cleanup` (server.py:3327) — move to `backup.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/{owner}/{repo_name}/branches/stale` (server.py:3384) — move to `backup.py`, delegate logic to service, keep handler HTTP-only
- [ ] `DELETE /api/projects/{owner}/{repo_name}/branches/{branch:path}` (server.py:3427) — move to `backup.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/delete-empty` (server.py:7673) — move to `backup.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/sprints/cleanup-empty` (server.py:7743) — move to `backup.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/deploy/promote` (server.py:13065) — move to `backup.py`, delegate logic to service, keep handler HTTP-only

### Cluster: `logs/activity`

Event ingestion, activity feed, run logs, dispatch logs, and live/stream SSE. Partially extracted (runs_router, log_search_router).

- **Target router module:** `apps/dashboard/routers/logs.py  (runs_router + log_search_router already mounted)`
- **Service module:** `apps/dashboard/routers/logs_service.py`
- **Repo/data layer:** events + agents + token_usage tables (SQLite) + dispatch-log files + SSE streams

**Per-endpoint checklist (14 endpoints):**

- [ ] `POST /api/agent-event` (server.py:1383) — move to `logs.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/token-usage` (server.py:1422) — move to `logs.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/agents` (server.py:1440) — move to `logs.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/events` (server.py:1445) — move to `logs.py`, delegate logic to service, keep handler HTTP-only
- [ ] `DELETE /api/events/test` (server.py:1450) — move to `logs.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /events` (server.py:1465) — move to `logs.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/projects/{slug}/events` (server.py:2081) — move to `logs.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/logs/runs` (server.py:6809) — move to `logs.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/logs/sync-github` (server.py:6934) — move to `logs.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/dispatch-log` (server.py:6946) — move to `logs.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/state-full` (server.py:6956) — move to `logs.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/issue/{issue_num}/log` (server.py:6997) — move to `logs.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/live` (server.py:7073) — move to `logs.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/sprints/{sprint_label}/live/stream` (server.py:7396) — move to `logs.py`, delegate logic to service, keep handler HTTP-only

### Cluster: `system/health`

Page shells (HTML routes), health/version/environment, GitHub auth + labels, alerts, docs-freshness, and static file serving. Smallest, mostly read-only — extract first.

- **Target router module:** `apps/dashboard/routers/system.py`
- **Service module:** `apps/dashboard/routers/system_service.py`
- **Repo/data layer:** config.py / env vars + github_client.py (auth status) + static files on disk

**Per-endpoint checklist (23 endpoints):**

- [ ] `GET /` (server.py:1168) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /home` (server.py:1174) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /overview` (server.py:1179) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /diagnostics` (server.py:1184) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /projects/{path:path}` (server.py:1194) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /project/{slug}` (server.py:1219) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /project/{slug}/analytics` (server.py:1225) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /project/{slug}/{tab}` (server.py:1232) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/health` (server.py:1240) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/environment` (server.py:1301) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/version` (server.py:1307) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/gh-auth-status` (server.py:1328) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/repo/config` (server.py:1526) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/github/labels` (server.py:1534) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/github/labels` (server.py:1552) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/alerts` (server.py:3722) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/alerts` (server.py:3728) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `DELETE /api/alerts/{idx}` (server.py:3737) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `POST /api/docs-freshness/check` (server.py:3763) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/docs-freshness/warnings` (server.py:3786) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `DELETE /api/docs-freshness/warnings/{warning_id}` (server.py:3791) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /api/home` (server.py:4170) — move to `system.py`, delegate logic to service, keep handler HTTP-only
- [ ] `GET /static/{filename:path}` (server.py:13400) — move to `system.py`, delegate logic to service, keep handler HTTP-only

