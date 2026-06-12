# AGENTS.md — services/sprint_manager

## Purpose

Sprint lifecycle management — dispatching tickets to coder/tester Claude Code
agents, tracking sprint state transitions, estimating issues, generating
sprint summaries, and maintaining the optional Neon/Postgres mirror of sprint
metadata. This is the brain of the Commander orchestration layer.

## Key Files

- `sprint_manager.py` — main orchestrator; runs sprints and dispatches tickets via Claude Code
- `state_machine.py` — sprint state transitions (`planning → running → finished`)
- `pipeline.py` — per-ticket execution pipeline with retry and backoff
- `estimate_issue.py` — per-issue size estimation (Haiku model)
- `models.py` — Pydantic models for sprint/ticket/estimate data
- `sprint_repo.py` — Neon/Postgres mirror for sprint metadata (optional, may be disabled)
- `settings_repo.py` / `settings_schema.py` — per-project settings storage
- `dag_builder.py` — dependency graph builder for ticket ordering
- `document_issue.py` — invokes the Documentor agent after a ticket ships

## Conventions

- **Neon is optional** — all Neon writes are wrapped in `try/except`; the dashboard runs fully without it.
- **`COMMANDER_DISABLE_NEON=1`** in `.env` skips all Neon operations entirely.
- **GitHub labels are source of truth** for sprint state — not local variables.
- **Estimates are cached** in `.commander/estimates/issue-<N>.json`; re-run with `--force` to bust.
- **Claude Code subprocess** — agents are dispatched as `claude` CLI subprocesses with `CLAUDE_AGENT_ROLE` set.

## Danger Zones

- `sprint_repo.py` — Neon schema may not be migrated on all machines; always wrap in try/except and log, never crash.
- `state_machine.py` — state transitions are effectively irreversible (GitHub labels are sticky); test before changing transition logic.
- `sprint_manager.py` dispatch loop — do not add blocking I/O or long sleeps in the hot path; it serializes ticket dispatches.
- `pipeline.py` retry logic — increasing retry counts raises API costs; coordinate with owner.

## What NOT to Touch

- `.commander/sprint.yaml` — human-authored project config; never auto-overwrite.
- `.commander/estimates/` — cached estimates; only the estimator writes here.
- `DATABASE_URL` env var — Neon connection string; never log, print, or expose.
- Sprint summary GitHub issues — once created they are the permanent sprint record; do not close or edit their bodies.
