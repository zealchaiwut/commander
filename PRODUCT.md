# Commander — Product Overview

Commander is a **personal AI agent platform for solo development with Claude Code**.
It tracks work as GitHub Issues through a BA → Coder → Tester → UAT loop, runs
agents from a FastAPI dashboard, and is designed so an overnight Claude Code /
Hermes session can drive sprints **only over HTTP** — never by spawning
privileged `claude -p` itself.

This file is the product strategy surface for Spec-Driven Development (SDD).
Business detail lives in discussion/ADR/milestone docs under `docs/`; technical
depth lives in `docs/architecture/`; HTTP contracts live in `docs/agent-guide.md`
and (planned) OpenAPI YAML. Visual / BA Design Refs live in `DESIGN.md`.

## Who it is for

| Persona | Need |
|---------|------|
| **Solo operator** | Plan and run sprints from the dashboard (Board / Running / History); sign off UAT; merge develop → master by hand |
| **Claude Code / Hermes (overnight)** | Call dispatch / overnight / complete APIs, poll status, stop cleanly — no elevated CLI spawn |
| **BA / Coder / Tester agents** | Ticket specs with AC + Design Refs; privileged spawn happens **inside** Commander |

## What it does

1. **GitHub-native sprint board** — Issues carry `sprint-N` labels; columns mirror backlog → in-progress → SIT → UAT → done.
2. **API dispatch queue** — `POST /api/sprints/{label}/dispatch` runs coder → tester per ticket (plus wrap-up reviewer/documentor); optional `"all": true` resolves open tickets by label.
3. **Overnight babysitter** — `POST …/overnight` owns reset + re-dispatch until done or exhausted so Claude Code only starts and polls.
4. **Running / live visibility** — Dashboard Running pane and live SSE read dispatch JSON + hooks (not the deleted orchestrator alone).
5. **Finish & UAT sign-off** — Merge sprint→develop (and optional per-sprint UAT close) via Finish / `complete-after-dispatch` / uat-signoff paths.
6. **Multi-project** — Sprint identity is `(label, project)`; `project=` is `owner/repo`.

## Design principles

- **Local-first, GitHub as source of truth for issues** — SQLite + local JSON for runtime; optional Neon is secondary / kill-switchable.
- **Single-user** — No accounts, sessions, or roles. Write APIs may use one static bearer token (`COMMANDER_API_TOKEN`); GETs/SSE stay open; localhost hooks exempt.
- **Privileged spawn stays in Commander** — Assistants must not run `--dangerously-skip-permissions`; they call HTTP.
- **Specs guide tickets** — PRODUCT (this file) + DESIGN.md + architecture/ADRs feed BA Design Refs and acceptance criteria.
- **Human owns production** — Only the operator merges `develop` → `master`.

## Explicit non-goals

- Multi-tenant auth / OAuth / per-role permissions  
- Discord / Slack notification systems (separate initiative)  
- Restoring the deleted overnight orchestrator / gates pipeline as a second runtime  
- Auto-merging develop → master  

## Related docs

| Doc | Role |
|-----|------|
| [`DESIGN.md`](DESIGN.md) | UI / Design Refs headings for BA |
| [`docs/requirements/`](docs/requirements/) | Capability IDs (REQ-*) indexed over ADRs/milestones |
| [`docs/api/overnight.yaml`](docs/api/overnight.yaml) | OpenAPI subset for overnight / Hermes HTTP |
| [`docs/agent-guide.md`](docs/agent-guide.md) | HTTP recipes for Hermes / Claude Code |
| [`docs/architecture/`](docs/architecture/) | Technical design chapters |
| [`docs/decisions/`](docs/decisions/) | ADRs (discussion outcomes) |
| [`docs/milestones/`](docs/milestones/) | Initiative trackers (e.g. shrink, lifecycle) |
| [`docs/features/`](docs/features/) | Subsystem reference (api, dashboard, sprint-manager) |
| [`CLAUDE.md`](CLAUDE.md) | Agent operating instructions (operational SoT) |
