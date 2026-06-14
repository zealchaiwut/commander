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
