# Code State — sprint-1023

_Deterministic snapshot generated at sprint finish. Do not hand-edit — regenerated each sprint._

## Module Map

- **`apps/`** — FastAPI web dashboard application and router modules
- **`apps/dashboard/routers/`** — Extracted FastAPI route modules (no new routes in server.py)
- **`apps/dashboard/static/src/`** — ES module source bundled via esbuild → static/dist/bundle.js
- **`services/`** — Sprint lifecycle management and agent orchestration
- **`services/sprint_manager/`** — Sprint orchestration, dispatch loop, and post-sprint pipeline
- **`scripts/`** — CLI helpers for ticket, branch, and sprint lifecycle operations
- **`tests/`** — Pytest test suite (unit and integration)
- **`hooks/`** — Event webhook handlers that POST to the dashboard
- **`docs/`** — Project documentation
- **`alembic/`** — Database migration scripts

## Recent Deltas (sprint-1023)

Files changed: **177**

- `tests/` — 145 file(s)
- `apps/` — 19 file(s)
- `services/` — 13 file(s)

## Hot Files (last 90 days)

| File | Commits |
|------|---------|
| `apps/dashboard/static/project.html` | 541 |
| `apps/dashboard/server.py` | 402 |
| `services/sprint_manager/sprint_manager.py` | 297 |
| `apps/dashboard/static/dist/bundle.js.map` | 219 |
| `apps/dashboard/static/dist/bundle.js` | 209 |
| `CHANGELOG.md` | 130 |
| `apps/dashboard/static/app.js` | 106 |
| `apps/dashboard/static/src/sprint-board/board-render.js` | 96 |
| `apps/dashboard/db.py` | 77 |
| `apps/dashboard/static/index.html` | 75 |
| `apps/dashboard/routers/__init__.py` | 55 |
| `README.md` | 52 |
| `apps/dashboard/startup.py` | 50 |
| `SCHEMA.md` | 49 |
| `apps/dashboard/static/src/sprint-board/history.js` | 49 |
| `docs/architecture/code-state.md` | 47 |
| `apps/dashboard/static/src/sprint-board/index.js` | 43 |
| `apps/dashboard/github_client.py` | 42 |
| `apps/dashboard/routers/sprint_history_service.py` | 39 |
| `services/sprint_manager/estimate_issue.py` | 38 |

## Generated

Sprint: `sprint-1023`  
Timestamp: `2026-08-13T01:02:52Z`  
_Generated deterministically — no LLM required._
