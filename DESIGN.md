# Commander — Design Document

Root design contract for Spec-Driven Development. **BA agents cite the `##`
headings below in ticket `## Design Refs`.** Deep system design lives under
[`docs/architecture/`](docs/architecture/); product strategy in
[`PRODUCT.md`](PRODUCT.md); HTTP overnight recipes in
[`docs/agent-guide.md`](docs/agent-guide.md).

When a ticket is UI-scoped, prefer the **UI surfaces** and **Visual system**
headings. When it is backend/lifecycle, prefer **Invariants** and the linked
architecture sections.

---

## Architecture map

Commander is a FastAPI dashboard (`apps/dashboard/`) plus sprint-manager
services (`services/sprint_manager/`), a vanilla JS board (`static/src/` →
esbuild `bundle.js`), SQLite for agents/events, and GitHub Issues as the issue
source of truth. Optional Neon/Postgres is secondary (`COMMANDER_DISABLE_NEON=1`
locally).

| Layer | Where to read more |
|-------|-------------------|
| State & SoT | [docs/architecture/1_state-and-source-of-truth.md](docs/architecture/1_state-and-source-of-truth.md) |
| Dashboard app | [docs/architecture/2_app-dashboard-architecture.md](docs/architecture/2_app-dashboard-architecture.md) |
| Sprint flow | [docs/architecture/3_sprint-flow.md](docs/architecture/3_sprint-flow.md), [sprint-lifecycle.md](docs/architecture/sprint-lifecycle.md) |
| Agents / dispatch | [docs/architecture/4_agents.md](docs/architecture/4_agents.md), shrink milestone |
| Multi-project | [docs/architecture/9_multiple-projects.md](docs/architecture/9_multiple-projects.md) |
| Security | [docs/architecture/12_security-and-secrets.md](docs/architecture/12_security-and-secrets.md) |

---

## Invariants

### Composite sprint key

Sprint identity is **`(label, project)`**, never `label` alone. All lifecycle
reads/writes must scope both fields (prevents cross-project collisions such as
commander `sprint-66` vs perf-coach `sprint-66`). See issue #1465 repair notes
in history and architecture §1 / §9.

### Project query parameter

Canonical `project=` / `repo` form is **`owner/repo`**. Bare slug is accepted
via central `project_resolver`. Unrecognised project → **404**, never a silent
default (#2064).

### Dispatch is a queue consumer

`POST /api/sprints/{label}/dispatch` executes tickets in the order given (or
ascending open issues when `"all": true`). It does **not** mint child sprint
labels, does **not** invent ordering beyond documented resolve rules, and does
**not** restore the deleted orchestrator. Overnight babysitter wraps dispatch +
reset (#2353–#2354). Shrink decisions:
[docs/milestones/commander-shrink-2026-08.md](docs/milestones/commander-shrink-2026-08.md).

### Sprint-branch model

Dispatch cuts `sprint/sprint-N` from develop; feature branches merge into the
sprint branch; one sprint→develop PR at the end (#2329). Human merges develop →
master.

### Privileged spawn stays in-process

Claude Code overnight and Hermes call HTTP only. `claude -p
--dangerously-skip-permissions` runs inside Commander’s dispatch runner, not in
the assistant session.

---

## UI surfaces

Headings in this section are the primary **Design Refs** targets for frontend
tickets.

### Sprint board

Board / Running / History sub-views. Cards show sprint label, ticket roster,
capacity, preflight, and **Run Sprint** (wires to dispatch API — #2356). No
instruction that `/coder` then `/tester` by hand is the only path.

### Running view

Surfaces active API dispatch runs from `.commander/runtime/dispatch-*.json`
alongside legacy plan/PID rows when present (#2355). Must not require a PID
file for API-only runs. Prefer poll `GET /api/sprints/dispatch/{run_id}` for
tick-level progress; SSE may emit `dispatch` events.

### Live snapshot and SSE

`GET …/live` and `…/live/stream` include dispatch fields (`run_id`,
`current_issue`, `current_step`, recent outcomes) when a run is active.

### Finish and sign-off

Finish / bulk-complete / UAT sign-off modals and History “sign off” actions.
Per-sprint UAT sign-off must include Executive Summary issues for that sprint
(#2305). Soft rework guard on finish: warn before closing non-UAT tickets.

### Logs and activity

Activity feed default; source chips; SSE for agent events. Zero surprise empty
states when `project`/`repo` is the full `owner/repo`.

### Settings and API token

Bearer token for writes is entered once (`commanderSetApiToken` / Settings) and
stored in `localStorage` — **never** inlined into served HTML (#1895).

---

## Visual system

Product UI bar: earned familiarity (dashboard density), not marketing chrome.
When impeccable / UI polish tickets apply:

### Spacing scale

Use **4 · 8 · 12 · 16 · 24 · 32** px for margin/padding/gap in board and logs
chrome (impeccable / audit tests).

### Typography

One well-tuned sans for product UI; fixed rem scale; tighter ratios (≈1.125–1.2).
Display fonts belong on brand surfaces, not dense board chrome.

### Color and state

Restrained accent for primary actions and selection. Explicit states: default,
hover, focus, active, disabled, loading, error, warning, success.

### Motion

150–250 ms for most transitions; prefer progress/skeleton over blocking
spinners in content.

### Responsive board

Structural collapse (columns, Running pane) at mobile breakpoints; Tailscale
mobile use is in-scope for the dashboard.

---

## Agent and ticket contracts

### Definition of Ready

Tickets need Acceptance Criteria, Design Refs (when DESIGN applies), UAT/test
plan, and size estimate per DoR mode (`block` / `warn` / `off`). Canonical
parser: `services/sprint_manager/ticket_spec.py`.

### Design Refs usage

BA lists only headings that **exist in this file**. Prefer UI surfaces for
frontend work; prefer Invariants + architecture links for backend. If this file
is absent in a non-Commander project, warn and skip Design Refs.

### Hermes / Claude Code HTTP

Overnight recipe and error conventions:
[docs/agent-guide.md](docs/agent-guide.md). Planned checked-in OpenAPI YAML
will mirror the Hermes-critical + overnight surface; until then, `/openapi.json`
plus the agent guide are authoritative.

---

## Test and verify (design-facing)

- Composite-key and project-scoped lifecycle tests  
- Dispatch-by-label / overnight / running-dispatch JSON / Run Sprint fetch spies  
- AC tests exercise behavior, not source-regex (#1746)  
- Frontend spacing audits against the scale above where applicable  
