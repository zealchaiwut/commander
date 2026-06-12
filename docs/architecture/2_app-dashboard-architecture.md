# 2. App / Dashboard architecture

> **Decision record · status: partial** — 2.2 backend split in progress (#761); 2.2b structured logging Phase 1 landed; 2.3 frontend build pipeline started (#796); **2.2a done** ([boundaries.md](boundaries.md)); **2.3a / 2.3b pending**.

[← Contents](0_content.md) · [← Prev: State & source-of-truth](1_state-and-source-of-truth.md) · [Next: Sprint flow →](3_sprint-flow.md)

## 2.1 Current structure

Verified on `develop` (2026-06-12). The original four-monolith diagnosis still holds — the names shifted as extraction started.

| Layer | File(s) | Size | Notes |
|-------|---------|------|-------|
| **Backend routes** | `apps/dashboard/server.py` | ~13.5k lines, **166 routes** | Still the bulk of HTTP surface; strangler-fig extraction to `routers/` underway (#761) |
| **Backend data** | `apps/dashboard/projects.py` | ~560 lines | GitHub/project registry data layer |
| **Backend orchestration** | `services/sprint_manager/sprint_manager.py` | ~8.9k lines | Sprint dispatch, gates, fix-loop |
| **Frontend markup** | `static/project.html` | ~25.6k lines | Single-page project view (replaced the old `index.html` monolith) |
| **Frontend logic** | inline in `project.html` + `static/src/` modules + `static/dist/bundle.js` | ~3k bundled | esbuild pipeline started (#796); most logic still inline |

**Diagnosis:** not wrong, but **unfactored**. Backend = unfactored-but-factorable (FastAPI `APIRouter` — extraction in flight). Frontend = unfactored *and* the no-build choice blocked the easy fix until recently (flat global scope in `project.html`; partial ES-module extraction under `static/src/sprint-board/`).

### Pain ranking (owner)

1. `project.html` (markup) · 2. inline JS / `static/src/` (logic) · 3. `server.py` (routes) · 4. `sprint_manager.py`. **Frontend is the priority.**

## 2.2 Backend split — YES (three-layer, modern MVC)

**Decision:** split into router → service → repository.

| Layer | Location | Responsibility |
|-------|----------|----------------|
| **Router** | `apps/dashboard/routers/*.py` | Thin HTTP only — parse request, call one service, shape response |
| **Service** | `apps/dashboard/routers/*_service.py` | Business logic / orchestration, **no FastAPI imports** |
| **Repository** | `repositories/*.py` + existing data modules | Sole store access (GitHub / SQLite / Neon / disk JSON) |

Trivial routes (health, version) may go router → repo directly. Template for the repo layer: Neon `sprint_repo.py` (#246).

Router clusters (from route map): `health`, `events`, `projects`, `sprints` (split read/write — biggest), `tickets`, `bulk`, `deploy`, `pages`. Target: `server.py` → thin app-factory (**<300 lines**).

### Implementation status

- `apps/dashboard/routers/` exists with 15+ router/service pairs mounted via `app.include_router`.
- Partial extractions live: `analytics`, `backup`, `log_search`, `runs`, `activity`, `brief`, `doctor`, `dispatch`, `sprint_history`, `sprints`, `tickets`, `todos`, …
- `COMMANDER_GATE_MONOLITH` CI gate blocks new routes in `server.py`.
- **Remaining:** ~130 endpoints still in the monolith; extraction order in [boundaries.md](boundaries.md).

### 2.2a Backend router/service/repo boundaries — DONE

The full route → service → repo mapping lives in **[boundaries.md](boundaries.md)** (166 endpoints, 8 clusters, extraction order, layer rules). This section does not duplicate that table — refer there when scoping extraction tickets.

## 2.2b Backend logging — structured logger, disk-first (Phase 1), Neon later (Phase 2)

**Problem:** "Can't investigate when things fail; sprint running but I can't see it." Observability gap, not formatting.

### Current (pre-/mid-migration)

| Surface | What it is |
|---------|------------|
| Per-run file | subprocess `print()` → `sprint-run-<label>-<ts>.log` |
| Per-issue file | agent dispatch log per ticket |
| Daily alert log | alert sidecar |
| SQLite `events` | dashboard event stream |

**Root causes:** unstructured text (no levels/IDs); four surfaces with no shared correlation key; "running but blank" = file-tail glob gaps; buffered stdout loses final lines on kill.

### Target record shape

```json
{
  "ts": "...",
  "level": "INFO",
  "run_id": "sprint12-20260528T1745-a3f",
  "source": "sprint",
  "agent_role": "coder",
  "issue_num": 42,
  "sprint_label": "sprint-68.6",
  "project": "zealchaiwut/commander",
  "git_sha": "abc123",
  "event": "coder.dispatch",
  "message": "..."
}
```

**Correlation key:** two-level **`run_id` (parent) + `issue_num` (child)**.

- `run_id` minted for **every** invocation — sprint *and* manual/ad-hoc (manual runs were invisible before).
- Format `<source>-<ts>-<shortrand>` (e.g. `sprint12-20260528T1745-a3f`, `manual-…-9c1`); **source prefix** = sprint / manual / adhoc.
- `sprint_label` = optional attribute, not the key.
- `git_sha` = optional forensic field (ties to build-version / cache-bust).
- Levels: DEBUG / INFO / WARN / ERROR for UI filtering. **Flush per record / line-buffered.**

Lives in infrastructure layer: services call `log.event(...)`, logger fans out to sinks. Routers don't log business events.

### Phases

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | `services/logging.py` + run_id + flush + levels, **disk sink only** | **Landed** — `generate_run_id()`, structured envelope, subprocess line wrapping |
| **Phase 2** | Neon sink → `run_events` + `ticket_events`; survives restarts, powers failure-pattern analysis | Pending (Neon telemetry tier, ranked #1/#2 in [section 13](13_observability-and-cost.md)) |

**Open:** whether Phase 1 collapses the four log surfaces immediately or during transition; file-tail vs true live-stream for the live log panel (deferred to [2.3b](#23b-frontend-sitemap--pageapi-binding--live-log--pending)).

## 2.3 Frontend split — phased, build step adopted

**Decision:** adopt a build step. Override the old CLAUDE.md "no build step / no frontend frameworks" rule (frameworks still deferred).

| Phase | Approach | Status |
|-------|----------|--------|
| **Now (middle path)** | esbuild **bundling only** + vanilla JS **ES modules** + **HTML partials**. No framework yet. Split inline JS, extract inline `<style>` blocks into real CSS, break up `project.html` markup. | **Started** (#796): `package.json`, `static/src/index.js`, `static/dist/bundle.js`, `static/src/sprint-board/*` |
| **Later (backlog)** | Full framework (React/Vue/Svelte) — middle path is a deliberate stepping stone | Not started |

**Accepted cost:** Node/npm becomes a hard dependency in every clone (prd, coder, tester, uat) and the remote setup.

### 2.3a Frontend module boundaries — PENDING

Define which ES modules own which concerns (board render, sprint controls, bulk-create, logs panel, settings, …). Sprint-board modules under `static/src/sprint-board/` are the seed; the bulk of `project.html` inline JS still needs a module map.

### 2.3b Frontend sitemap + page→API binding + live log — PENDING

Partial coverage exists in **[frontend-map.md](frontend-map.md)** (tabs, sub-views, API bindings for router extraction #793). Still needed:

- Complete module-boundary map (pairs with 2.3a)
- **Settle file-tail vs true live-stream** for the Live View log panel (deferred from 2.2b)
- Framework choice (React/Vue/Svelte) — backlog, not blocking middle path

## 2.4 Route & naming cleanup — strangler-fig

**Decision:** keep legacy + new routes alive until the new family reaches **full parity**, then **delete legacy in one cut**.

Targets:

| Drift | Example |
|-------|---------|
| Singular vs plural project routes | `/api/project-*` vs `/api/projects/*` |
| Issues vs tickets naming | `/api/issues/*` vs `/api/tickets/*` |
| Running sprint queries | `/api/sprints/running` vs `/api/sprints/running-all` |

See [frontend-map.md](frontend-map.md) for the current binding map used to verify parity before deletion.

## 2.5 Sequencing — refactor before any more features

**Decision:** in-flight UI sprint finishes (already running). After that, **no new feature work** (bulk-create-tab, UI parity, Neon features) until the refactor lands.

Recommended refactor sprint scope:

1. Characterization tests
2. Backend router/service/repo split (continue #761)
3. Structured logging Phase 1 (complete surface consolidation)
4. Frontend Vite/esbuild + modules + partials split
5. Legacy route deletion at parity

**Open:** refactor as one sprint vs split (frontend/backend) — decide at scoping.

## Action items

- [x] **Fix `.env` gitignore gap** — `.env` and `.env.*` now in `.gitignore` (was missing at time of review; README claim now accurate)
- [x] **Update CLAUDE.md** — permit esbuild bundling + ES modules (frameworks still deferred) — see commit on this branch
- [x] **2.2a route mapping** — [boundaries.md](boundaries.md) (#793)
- [ ] **2.3a** — frontend module boundaries
- [ ] **2.3b** — complete sitemap + live-log stream decision
- [ ] Refactor sprint: characterization tests → backend split → logging Phase 1 completion → frontend split → legacy route deletion
- [ ] Use Neon `sprint_repo.py` (#246) as repository-layer template for remaining clusters
- [ ] Manual agent entry-points must mint a `run_id` + use the structured logger
