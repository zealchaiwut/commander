# 1. State & source-of-truth model

*The foundation — every other section depends on this.*

[← Contents](0_content.md) · [Next: App / Dashboard architecture →](2_app-dashboard-architecture.md)

> **Target contract:** [sprint-lifecycle.md](sprint-lifecycle.md) (agreed design, P0/P1 landing). This section describes today's stores and the migration direction.

## 1.1 The stores today

| Store | Location | What it holds |
|-------|----------|---------------|
| **GitHub Issues** | `github.com/<owner>/<repo>/issues` | Ticket titles, bodies, open/closed, **labels** (`backlog`, `in-progress`, `SIT`, `UAT`, `needs-rework`, `sprint-N`, …), comments, PR links, summary issues |
| **GitHub PRs / branches** | Remote git | Feature branches, sprint branches (`sprint/sprint-N`), merge state, `attachments` branch |
| **SQLite** (`DB_PATH`) | `commander.db` (per dashboard instance / clone) | See per-table breakdown below — lifecycle (`sprints`), metrics (`agent_runs`, `token_usage`), events, GitHub replicas (`issues` mirror, `ticket_status`, `milestones`), ledger (`sprint_history`) |
| **Neon Postgres** (optional) | `DATABASE_URL` | **Export/analytics target only** — no runtime read/write path remains; `sprint_repo.py` is called only by migration/export scripts. `COMMANDER_DISABLE_NEON=1` also disables the settings/todo KV fallback |
| **Disk JSON** | `.commander/sprints/*`, `bulk-jobs/*`, `estimates/*`, plus `projects.json`, `settings_store.json`, `calibration_cache.json`, `mis-sizing-flags-*.json`, `runtime/sprint-progress.json` | Sprint plan files (`-plan.json`, incl. dual-written lifecycle state + `signoff`), live run state (`-state.json`), status sidecars (`-status.json`), summary markdown, bulk-create job state, cached estimates, project registry |
| **Runtime memory** | `server.py` process | `_sprint_statuses` (manager POSTs), bulk jobs, `github_client._cache` (30 s base TTL, longer per prefix), `_home_cache` — **lost on restart** unless lazy-loaded from disk |
| **Process state** | PID files (`{label}-pid`), `COMMANDER_SPRINT_RUNNING` env | Active sprint manager subprocess, orphan detection |

### SQLite per-table roles

| Table(s) | Role | Writer | Authority |
|----------|------|--------|-----------|
| `sprints` (PK `(label, project)`) | **Sprint lifecycle state** + end-of-run artifacts (issues_json, tokens, wall clock, reconciliation, pr_number) | `db.transition_sprint_state()` only (actor-guarded); `db.ingest_sprint_run_artifact()` at end-of-run | **Authoritative for sprint lifecycle** |
| `agent_runs` | One row per dispatched agent: timings, outcome, model, SHAs | Sprint manager | Primary (metrics) |
| `sprint_history` | Append-only terminal/deleted sprint ledger snapshots | `record_sprint_history` (finish/delete) | Primary — the only home of `deleted`; **not** a GitHub mirror despite the old name |
| `issues` + `sync_state` | **GitHub issues mirror** — full gh-shaped issue dicts per repo + ETags | `github_events_sync` 60 s loop (zero-quota 304 polls; open-set reconcile every ~20 sweeps) | Replica of GitHub |
| `ticket_status` | Write-through of every successful label transition | `state_machine.transition()` | Replica of GitHub labels |
| `milestones` | Milestones mirror | Same sync loop | Replica |
| `agents`, `session_events`, `events`, `project_events`, `token_usage` | Live agent presence, activity feed, token/cost rows | Hooks + manager + dashboard | Primary (local-only) |
| `sprint_ticket_order` | Dispatch order | Manager | Primary |
| `settings_kv`, `advisor_*`, `brief_*` | Settings KV fallback, advisor drafts, brief cache | Respective services | Primary |

Note: the **sprint manager subprocess imports `apps/dashboard/db.py` directly**
and writes the same SQLite file the server reads, best-effort with swallowed
exceptions — there is no queue or IPC layer between the two writers.

## 1.2 Who is authoritative for what

| State | Authoritative store | Notes |
|-------|---------------------|-------|
| Ticket workflow label | **GitHub** | Single writer: `state_machine.transition()` (write-through to `ticket_status`; readable via `issues` mirror ≤60 s stale) |
| Sprint membership (`sprint-N` label) | **GitHub** | Assigned at sprint create; frozen during run (`RUN_MUTABLE_LABELS` guard) |
| Sprint lifecycle (`draft` → `running` → `completed`, …) | **SQLite `sprints`** (implemented) | Single reader `sprint_state.current()`, single writer `db.transition_sprint_state()`; GitHub labels are reconcile *inputs*, not a lifecycle store; plan.json is a deprecated dual-write still read on some paths — see [sprint-lifecycle.md](sprint-lifecycle.md) |
| Run metrics (durations, tokens, gantt) | **SQLite** | GitHub does not store wall-clock or per-agent timings |
| Project registry | **`projects.json`** | Neon copy exists only via `scripts/export_to_neon.py`; no startup sync |
| Settings | **Local JSON / `settings_kv`** (Neon KV only when enabled) | Shallow merge: project overrides global |
| Bulk-create job progress | **Disk JSON** (with in-memory cache) | Server restart lazy-loads from `bulk-jobs/{id}.json` |
| Sprint sign-off (pending/approved) | **plan.json `signoff` field** | Outside the lifecycle enum — the `planned` state was never wired |

**Conflict rule (target):** GitHub wins for *state*; local DB wins for *metrics*; disk artifacts are write-once run records ingested at end-of-run, not read at render time.

## 1.3 Reconciliation

**Three distinct systems share the word "reconcile"** — do not conflate them:

| # | System | Module | Writes |
|---|--------|--------|--------|
| A | **Lifecycle reconcile** (GitHub ↔ DB sprint state) | `apps/dashboard/routers/sprint_reconcile_service.py` | DB `sprints` rows + plan.json (superseded orphans only) — **never GitHub** |
| B | **Post-sprint loose-ends checks** (summary issue / sprint PR merged / stale status labels) | `services/sprint_manager/reconciliation.py` | `<label>-state.json` + DB `reconciliation_json` — report-only, never changes lifecycle |
| C | **Mirror open-set reconcile** (closes stale-open mirror rows) | `github_events_sync.reconcile_closed_issues` | Local `issues` table only; every ~20 sync sweeps (~20 min) |

### System A — lifecycle reconcile

**Triggers:** (1) background sweep scheduled by `GET /api/sprints/history?project=`
— the *only* auto trigger; nothing runs on Board load or a timer; (2) per-sprint
button: `GET .../reconcile-preview` (pure dry-run, no writes) and
`POST .../reconcile` (apply); (3) the sprint manager runs system B (not A) at
end-of-run via live `gh`.

**Gating:** `COMMANDER_DISABLE_AUTO_RECONCILE=1` kills the background sweep
only (button still works — intended for non-primary clones); per-project 60 s
in-process TTL (timestamp recorded *before* the pass, so a failed pass still
consumes the window); sweep capped at 40 rows; `running / draft / planned /
completed / deleted` rows are skipped — only `ready_to_merge / needs_rework /
failed / cancelled` are re-checked.

**Decision logic** (`_github_reconcile_row` — pure, patch applied via
`db.transition_sprint_state(actor="reconcile")`):

- Truth signal is `_has_rework_tickets`: among **open** issues whose primary
  sprint label matches, any non-summary ticket carrying a rework label or not
  carrying `UAT` ⇒ rework. (Closed-without-UAT tickets vanish from the signal.)
- `ready_to_merge`/`completed` + rework → demote to `needs_rework`, unless
  `end_reason == "natural"` and stored `issues_json` shows everything merged
  (label-lag guard).
- `needs_rework`/`failed`/`cancelled` + no rework → promote to
  `ready_to_merge`. Stale `issues_json` failure flags are deliberately not
  trusted for this direction.
- `needs_rework → completed` is never sweep-driven; it happens only through
  Merge Sprint / bulk-complete / complete-step (which use `actor="reconcile"`
  for superseded ancestors after verifying the lineage merged to develop).
- Orphaned `running` rows (PID file present, process dead) settle to
  `ready_to_merge`/`needs_rework` with `end_reason=reconcile-orphan` — but
  because the sweep skips `running` rows, **orphan settling only happens via
  the per-sprint button** in practice.

**Inputs are mirror-backed:** ticket labels + summary issues from the local
`issues` table; PR merge state inferred from one cached
`gh pr list --state merged --limit 200` per repo (30 s TTL). A History load
therefore no longer fans out `gh` calls per sprint.

Count repair: `_reconcile_counts` re-derives per-issue states from
`agent_runs`, never downgrading a positive state, and persists into
`issues_json`.

### System C — issues mirror freshness

`run_issues_sync_loop` (startup) sweeps every repo every 60 s: conditional
`GET /issues?state=all&sort=updated` with `If-None-Match` — 304 = zero quota;
200 = upsert + new ETag in `sync_state`. Bootstrap crawls up to 5,000 issues.
Reads go through `github_client._mirror_issues` and fall back to live `gh`
(30 s cache) when the mirror is empty. Staleness window: ≤60 s normally; a
closure that pages off the 100-row conditional window waits for the ~20 min
open-set reconcile.

### Startup recovery

`_restore_sprint_statuses_on_startup()` rehydrates in-memory tracking from
disk `-status.json`/`-state.json` files. Orphan-PID detection settles lost
processes per the rules above.

## 1.4 Neon — export target, not a runtime store

Status as of 2026-07-02: **no runtime code path reads or writes Neon sprint
data.** `sprint_repo.py` (sprint/ticket CRUD, rollups) is imported only by
`scripts/migrate_sprints_to_neon.py`; project sync exists only in
`scripts/export_to_neon.py`; there is no startup projects sync. The only
runtime Neon touchpoints are the settings/todo KV repos, and both prefer local
SQLite/JSON when `COMMANDER_DISABLE_NEON=1` (set on the local machines).

The earlier "Neon holds lifecycle for instant render" target was superseded:
SQLite `sprints` took that role. *(open question: delete
`sprint_repo.py`/`models.py` or explicitly keep them as export-only —
leaving them wired invites re-connection against stale assumptions.)*

## 1.5 Four-store contract

The table below is the single reference for how each store is used. Do not add
a new read or write path without updating this table.

| Store | Role | Read path | Write path |
|-------|------|-----------|------------|
| **GitHub** (Issues / PRs) | Authoritative state — ticket labels, sprint membership, open/closed status | Primary: SQLite `issues` **mirror** (60 s ETag sync) via `github_client._mirror_issues`; fallback: live `gh` with in-process cache `github_client._cache` (30 s base TTL, 300 s for labels/sprints, 120 s summary issues). Merged-PR state: cached `gh pr list --state merged` | `state_machine.transition()` only — no direct label writes outside the state machine (plus finish/bulk-complete closing issues, and sprint create/rerun label management) |
| **SQLite** (`commander.db`) | Sprint lifecycle (authoritative) + metrics — run durations, token counts, sprint ledger, agent events, GitHub replicas | `db.*` helpers in `apps/dashboard/db.py`; lifecycle via `sprint_state.current()` | Lifecycle: `db.transition_sprint_state()` (actor-guarded) from server AND manager subprocess; artifacts via `db.ingest_sprint_run_artifact()`; hooks via `db.*` |
| **Disk JSON** (`.commander/sprints/*`, `bulk-jobs/*`, `estimates/*`) | Live-run scratch (`-state.json`, `-status.json`) + dual-written plan state + write-once artifacts (summary md, estimates) | Target: lazy-load on cache miss only. **Reality: several render paths still read disk** — see §1.7 | Sprint manager throughout the run (state/status/plan) and at end-of-run; server writes plan state on run/finish |
| **Neon Postgres** (optional) | Export / analytics only — no runtime path (§1.4) | Scripts only | `scripts/migrate_sprints_to_neon.py`, `scripts/export_to_neon.py`; settings/todo KV when enabled |

### Conflict resolution (canonical)

When stores disagree, apply these rules in order:

1. **GitHub wins for ticket state** — ticket labels and sprint membership are what GitHub says, regardless of what SQLite or disk hold. The `issues` mirror and `ticket_status` are replicas; reconcile (system A) folds GitHub back into the DB, never the reverse.
2. **SQLite `sprints` wins for sprint lifecycle** — `sprint_state.current()` is the sole sanctioned reader; plan.json's `state` field is a deprecated dual-write.
3. **SQLite is the single read path for metrics at render** — durations, token counts, and history rows come from DB helpers only. If the DB row is missing, show a missing-data indicator, never fall back to disk.
4. **Disk is write-once audit after end-of-run** — run artifacts are ingested into SQLite at end-of-run; live-run files (`-state.json`, `-status.json`) are the manager's scratch and restart-recovery channel.
5. **Neon is export-only** — no runtime render path may depend on it.

## 1.6 Metric definitions and pane rules

### Canonical settled-done count

> **settled** = tickets that have moved past SIT: `uat + done + needs-rework`  
> Formula: `total − backlog − in-progress − sit`  
> Canonical helper: **`_settled_done_from_columns(total, columns)`** in `apps/dashboard/startup.py` (mirrored in `routers/sprint_artifact_service.py`, `routers/sprint_nav.py`; `server.py` is now a thin factory)  
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

### Known deviations (as of 2026-07-02) — target vs reality

The rules above are the **target invariant**; these render paths still read
disk today *(open question: migrate or bless as sanctioned fallbacks?)*:

| Endpoint | Disk read |
|----------|-----------|
| `GET /api/sprints/history` | Label discovery for non-DB-backed sprints via `-state.json`/`-plan.json` (`_record_from_files`); expanded-card issue synthesis reads state files. History assembly merges ledger + lifecycle rows + disk + `agent_runs` with rank heuristics — exactly the multi-source reconciliation §1.7 forbids, with authority embedded in `_merge_history_record` |
| `GET /api/sprints/{label}/live` | `-state.json` fallback when the in-memory status is missing (port-coupling escape hatch) + plan.json |
| `GET /api/sprint-progress` (nav pill) | `-state.json` fallback, explicitly commented as intentional |
| `GET /api/home` | Globs and parses newest `*-summary-*.md` at render |
| `POST /api/sprints/run` | plan.json ticket-roster fallback |

Background/apply reconcile paths also read/write plan.json, state.json, and
PID files — within the spirit (not render), but not the letter, of "zero disk".
