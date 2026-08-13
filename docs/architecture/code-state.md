# Code State — sprint-1024.1

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

## Recent Deltas (sprint-1024.1)

Files changed: **60**

- `tests/` — 27 file(s)
- `apps/` — 19 file(s)
- `scripts/` — 5 file(s)
- `services/` — 4 file(s)
- `(root)/` — 3 file(s)
- `docs/` — 2 file(s)

## Hot Files (last 90 days)

| File | Commits |
|------|---------|
| `apps/dashboard/static/project.html` | 545 |
| `apps/dashboard/server.py` | 405 |
| `services/sprint_manager/sprint_manager.py` | 297 |
| `apps/dashboard/static/dist/bundle.js.map` | 221 |
| `apps/dashboard/static/dist/bundle.js` | 211 |
| `CHANGELOG.md` | 132 |
| `apps/dashboard/static/app.js` | 106 |
| `apps/dashboard/static/src/sprint-board/board-render.js` | 97 |
| `apps/dashboard/db.py` | 79 |
| `apps/dashboard/static/index.html` | 75 |
| `apps/dashboard/routers/__init__.py` | 58 |
| `README.md` | 53 |
| `apps/dashboard/startup.py` | 51 |
| `SCHEMA.md` | 50 |
| `docs/architecture/code-state.md` | 50 |
| `apps/dashboard/static/src/sprint-board/history.js` | 49 |
| `apps/dashboard/static/src/sprint-board/index.js` | 44 |
| `apps/dashboard/github_client.py` | 42 |
| `apps/dashboard/routers/sprint_history_service.py` | 39 |
| `services/sprint_manager/estimate_issue.py` | 38 |

## Generated

Sprint: `sprint-1024.1`  
Timestamp: `2026-08-13T09:24:45Z`  
_Generated deterministically — no LLM required._
