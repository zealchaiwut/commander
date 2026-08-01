# Code State — sprint-1010

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

## Recent Deltas (sprint-1010)

Files changed: **2**

- `apps/` — 1 file(s)
- `tests/` — 1 file(s)

## Hot Files (last 90 days)

| File | Commits |
|------|---------|
| `apps/dashboard/static/project.html` | 528 |
| `apps/dashboard/server.py` | 396 |
| `services/sprint_manager/sprint_manager.py` | 281 |
| `apps/dashboard/static/dist/bundle.js.map` | 203 |
| `apps/dashboard/static/dist/bundle.js` | 193 |
| `CHANGELOG.md` | 111 |
| `apps/dashboard/static/app.js` | 106 |
| `apps/dashboard/static/src/sprint-board/board-render.js` | 90 |
| `apps/dashboard/static/index.html` | 75 |
| `apps/dashboard/db.py` | 70 |
| `README.md` | 51 |
| `apps/dashboard/routers/__init__.py` | 50 |
| `apps/dashboard/static/src/sprint-board/history.js` | 47 |
| `SCHEMA.md` | 44 |
| `apps/dashboard/startup.py` | 41 |
| `apps/dashboard/github_client.py` | 40 |
| `apps/dashboard/static/src/sprint-board/index.js` | 39 |
| `apps/dashboard/routers/sprint_history_service.py` | 38 |
| `services/sprint_manager/estimate_issue.py` | 37 |
| `apps/dashboard/static/home.html` | 33 |

## Generated

Sprint: `sprint-1010`  
Timestamp: `2026-08-01T14:08:19Z`  
_Generated deterministically — no LLM required._
