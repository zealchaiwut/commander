# 1. State & source-of-truth model

*The foundation — every other section depends on this.*

[← Contents](0_content.md) · [Next: App / Dashboard architecture →](2_app-dashboard-architecture.md)

> **Target contract:** [sprint-lifecycle.md](sprint-lifecycle.md) (agreed design, P0/P1 landing). This section describes today's stores and the migration direction.

## 1.1 The stores today

| Store | Location | What it holds |
|-------|----------|---------------|
| **GitHub Issues** | `github.com/<owner>/<repo>/issues` | Ticket titles, bodies, open/closed, **labels** (`backlog`, `in-progress`, `SIT`, `UAT`, `needs-rework`, `sprint-N`, …), comments, PR links |
| **GitHub PRs / branches** | Remote git | Feature branches, sprint branches (`sprint/sprint-N`), merge state |
| **SQLite** (`DB_PATH`) | `commander.db` (per dashboard instance) | Agent runs (`agents`), structured events (`events`), token usage (`token_usage`), sprint history mirror (`sprint_history`), project events, settings cache |
| **Neon Postgres** (optional) | `DATABASE_URL` | Sprint/ticket rows (`sprints`, `sprint_tickets`), settings KV, projects registry — **secondary**; disable with `COMMANDER_DISABLE_NEON=1` |
| **Disk JSON** | `.commander/sprints/*`, `.commander/bulk-jobs/*`, `.commander/estimates/*` | Sprint plan files, run status sidecars, bulk-create job state, cached estimates |
| **Runtime memory** | `server.py` process | In-flight bulk jobs, sprint status caches, GitHub 30s label cache — **lost on restart** unless lazy-loaded from disk |
| **Process state** | PID files, `COMMANDER_SPRINT_RUNNING` env | Active sprint manager subprocess, orphan-PID watchdog |

## 1.2 Who is authoritative for what

| State | Authoritative store | Notes |
|-------|---------------------|-------|
| Ticket workflow label | **GitHub** | Single writer: `state_machine.transition()` |
| Sprint membership (`sprint-N` label) | **GitHub** | Assigned at sprint create; frozen during run (`RUN_MUTABLE_LABELS` guard) |
| Sprint lifecycle (`draft` → `running` → `completed`, …) | **GitHub + DB** (target) | Today multiple readers disagree — see [sprint-lifecycle.md](sprint-lifecycle.md) |
| Run metrics (durations, tokens, gantt) | **SQLite / Neon** | GitHub does not store wall-clock or per-agent timings |
| Project registry | **`projects.json` + dashboard** | Neon mirrors on startup when enabled |
| Settings | **Neon or local JSON** | Shallow merge: project overrides global |
| Bulk-create job progress | **Disk JSON** (with in-memory cache) | Server restart lazy-loads from `bulk-jobs/{id}.json` |

**Conflict rule (target):** GitHub wins for *state*; local DB wins for *metrics*; disk artifacts are write-once run records ingested at end-of-run, not read at render time.

## 1.3 Reconciliation

Known drift patterns and mitigations:

| Symptom | Cause | Mitigation |
|---------|-------|------------|
| Same sprint shows `completed` and `cancelled` | Same-label re-dispatch under one label | **P0 fix:** child sprint per re-run (#894); no same-label redispatch |
| History pane `0 tickets` vs board showing tickets | DB row stale; render-time disk read racing manager | Stale-while-revalidate + background GitHub reconcile (#805, lifecycle redesign) |
| Bulk create stuck on "Posting…" | `_bulk_jobs` in-memory cleared on restart | Lazy reload from disk JSON |
| Activity tab empty | `project` key mismatch in events API | Fixed: use full `owner/repo` path |
| Cross-project sprint lock | Global `_any_sprint_running()` | Fixed: per-project lock |

Startup: `_restore_sprint_statuses_on_startup()` rehydrates in-memory tracking from disk status files. Orphan-PID sweep marks lost processes as `needs_rework`.

## 1.4 Target model — what Neon changes about authority

Neon is a **mirror**, not the primary runtime store today:

- When enabled: sprint/ticket rows sync for analytics, settings KV, cross-machine settings sync.
- When disabled (`COMMANDER_DISABLE_NEON=1`): dashboard runs on GitHub + SQLite + local JSON only.
- **Target (section 8.2):** Neon holds sprint lifecycle + ticket order for instant UI render; GitHub remains label authority; reconciliation on every refresh closes drift within seconds.

Until schema is migrated on all machines, treat Neon as optional and wrap all writes in try/except — never crash the dashboard on a Neon failure.

## 1.5 Four-store contract

The table below is the single reference for how each store is used. Do not add
a new read or write path without updating this table.

| Store | Role | Read path | Write path |
|-------|------|-----------|------------|
| **GitHub** (Issues / PRs) | Authoritative state — ticket labels, sprint membership, open/closed status | `GET /repos/:owner/:repo/issues` via `gh` or MCP; cached 30 s in `_github_label_cache` | `state_machine.transition()` only — no direct label writes outside the state machine |
| **SQLite** (`commander.db`) | Metrics — run durations, token counts, sprint history, agent events | Single read path at render time: `db.*` helpers in `apps/dashboard/db.py`; **never disk at render** | Sprint manager and hook scripts via `db.*` helpers; REST endpoints via `db.ingest_sprint_run_artifact()` |
| **Disk JSON** (`.commander/sprints/*`, `bulk-jobs/*`, `estimates/*`) | Write-once audit — sprint plan files, run status sidecars, bulk-create job state, estimates cache | Lazy-load on cache miss only (e.g. `_get_bulk_job()` on restart); **do not read disk at render** | Sprint manager end-of-run; scripts write sidecars; never rewritten once closed |
| **Neon Postgres** (optional) | Export / analytics — sprint/ticket rows, cross-machine settings KV, projects registry | Disabled by default (`COMMANDER_DISABLE_NEON=1`); when enabled, reads from `sprint_repo.py` helpers | `sprint_repo.py` on sprint create/complete; wrapped in `try/except` — Neon failure must never crash the dashboard |

### Conflict resolution (canonical)

When stores disagree, apply these rules in order:

1. **GitHub wins for state** — ticket labels and sprint membership are what GitHub says, regardless of what SQLite or disk hold.
2. **SQLite is the single read path for metrics at render** — durations, token counts, and history rows come from DB helpers only. If the DB row is missing, show a missing-data indicator, never fall back to disk.
3. **Disk is write-once audit** — disk artifacts are ingested into SQLite at end-of-run. After ingestion, disk is not read again at render time.
4. **Neon is an optional mirror** — when enabled, Neon may supplement analytics, but all runtime render paths fall back to SQLite if Neon is unavailable.

## 1.6 Metric definitions and pane rules

### Canonical settled-done count

> **settled** = tickets that have moved past SIT: `uat + done + needs-rework`  
> Formula: `total − backlog − in-progress − sit`  
> Canonical helper: **`_settled_done_from_columns(total, columns)`** in `apps/dashboard/server.py`  
> Frontend mirror: **`_snavSettledDone()`** in `apps/dashboard/static/project.html`

The old `done + uat` formula undercounted needs-rework; `total − backlog` overcounted by treating in-progress + SIT as done. Both are incorrect and must not be used.

### Which panes use which metric

| Pane | Metric | Why |
|------|--------|-----|
| **Donut center** (sprint nav panel) | `completed` (done + uat) | Shows how many tickets crossed the finish line — in-flight work excluded |
| **Sprint nav pill** | `settled` (`_settled_done_from_columns`) | Reflects mid-sprint forward progress including needs-rework tickets |
| **Sidebar badge** | `settled` | Same reason as pill |
| **Board running badge** | `settled` | Same reason as pill |
| **History / outcome panes** | DB-backed counts from `sprint_history` / `agent_runs` | Post-run accounting; not a live GitHub count |

> **Invariant:** Donut center shows COMPLETED % (done + uat); the pill/badge show PROCESSED (settled). They are intentionally different. Do not unify them.

## 1.7 Render-time read rules

**Do not read disk at render.** Disk artifacts (plan files, state sidecars, status JSON) are write-once records produced by the sprint manager. Reading them at HTTP request time introduces races with the manager and causes the disagreements documented in §1.3.

Rules for all render-path code:

- **Zero disk reads** in HTTP handlers. No `plan.json`, no `-state.json`, no `-status.json` reads inside `@app.get` / `@app.post` handlers.
- **Zero label inference.** Do not infer sprint state from GitHub label names. Use `sprint_state.current(label)` (see [sprint-lifecycle.md § Canonical Read Contract](sprint-lifecycle.md)).
- **Zero multi-source reconciliation at render.** Pick one store (DB for metrics, GitHub for state) and return its data. Reconciliation runs in the background, not in the HTTP path.

If no SQLite row exists for a requested artifact, return 404 or `no_data`. Ingestion runs at end-of-run only (sprint manager), never in response to HTTP requests — the on-demand path was removed in #1161.
