# Code State — sprint-1019

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

## Recent Deltas (sprint-1019)

Files changed: **18**

- `tests/` — 10 file(s)
- `apps/` — 4 file(s)
- `(root)/` — 2 file(s)
- `docs/` — 1 file(s)
- `scripts/` — 1 file(s)

## Hot Files (last 90 days)

| File | Commits |
|------|---------|
| `apps/dashboard/static/project.html` | 537 |
| `apps/dashboard/server.py` | 400 |
| `services/sprint_manager/sprint_manager.py` | 289 |
| `apps/dashboard/static/dist/bundle.js.map` | 209 |
| `apps/dashboard/static/dist/bundle.js` | 199 |
| `CHANGELOG.md` | 123 |
| `apps/dashboard/static/app.js` | 106 |
| `apps/dashboard/static/src/sprint-board/board-render.js` | 91 |
| `apps/dashboard/static/index.html` | 75 |
| `apps/dashboard/db.py` | 73 |
| `apps/dashboard/routers/__init__.py` | 52 |
| `README.md` | 51 |
| `apps/dashboard/startup.py` | 48 |
| `apps/dashboard/static/src/sprint-board/history.js` | 48 |
| `SCHEMA.md` | 45 |
| `apps/dashboard/github_client.py` | 42 |
| `apps/dashboard/static/src/sprint-board/index.js` | 40 |
| `docs/architecture/code-state.md` | 39 |
| `apps/dashboard/routers/sprint_history_service.py` | 38 |
| `services/sprint_manager/estimate_issue.py` | 37 |

## Generated

Sprint: `sprint-1019`  
Timestamp: `2026-08-04T05:13:18Z`  
_Generated deterministically — no LLM required._
