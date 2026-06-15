# AGENTS.md — apps/dashboard

## Purpose

The FastAPI web dashboard that serves the Commander UI, handles API requests,
tracks agent runs in real time, and manages sprint state. Delivers live updates
via Server-Sent Events. This is the monolith being progressively extracted into
`routers/` modules.

## Key Files

- `server.py` — main FastAPI app (~11k lines); the route monolith; **do not add routes here**
- `config.py` — env var loading (`DB_PATH`, `GITHUB_TOKEN`, `COMMANDER_DISABLE_NEON`, etc.)
- `db.py` — SQLite helpers and table definitions (`agents`, `events`, `token_usage`)
- `github_client.py` — GitHub API client with 30-second in-memory cache
- `projects.py` — project discovery, slug resolution, and project list management
- `run_server.py` — uvicorn launcher entry point with signal handling
- `routers/` — extracted route modules; all new endpoints go here

## Conventions

- **New endpoints** belong in `routers/<area>.py`, not `server.py`. See `COMMANDER_GATE_MONOLITH`.
- **Database access** — use helpers from `db.py`; no raw `sqlite3` calls in route handlers.
- **GitHub API** — always go through `github_client.py`; the 30s cache lives there.
- **SSE streaming** — follow the `EventSourceResponse` pattern already in `server.py`.
- **Pydantic v2** — all request/response models are Pydantic v2; use `model_validate`, not `.parse_obj`.
- **Auth** — none; single-user local deployment; do not add auth middleware.

## Danger Zones

- `server.py` line count — `COMMANDER_GATE_MONOLITH` CI gate fails if it grows; adding a route here will bounce the ticket back to SIT.
- `db.py` schema changes — require a migration; never `ALTER TABLE` in-place on a live DB.
- `github_client.py` cache — the 30s TTL is intentional; do not lower it without understanding the GitHub API quota implications.
- SSE `/api/sse` endpoint — used by all live views; breaking its event format breaks the entire UI.

## What NOT to Touch

- `server.py` — read it to understand patterns; do not add lines.
- `dashboard.db` — runtime data file; never commit it.
- `.env` — secrets; never commit; use `.env.example` for documentation.
- The `GITHUB_TOKEN` env var — never log, print, or expose in API responses.

<!-- needs-review: hotfix/board-history-running-ux — directory had changes; review and update this file -->
