# Code State — sprint-1022.3

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

## Recent Deltas (sprint-1022.3)

Files changed: **86**

- `tests/` — 37 file(s)
- `apps/` — 24 file(s)
- `services/` — 18 file(s)
- `(root)/` — 3 file(s)
- `docs/` — 2 file(s)
- `scripts/` — 2 file(s)

## Hot Files (last 90 days)

| File | Commits |
|------|---------|
| `apps/dashboard/static/project.html` | 540 |
| `apps/dashboard/server.py` | 402 |
| `services/sprint_manager/sprint_manager.py` | 295 |
| `apps/dashboard/static/dist/bundle.js.map` | 216 |
| `apps/dashboard/static/dist/bundle.js` | 206 |
| `CHANGELOG.md` | 129 |
| `apps/dashboard/static/app.js` | 106 |
| `apps/dashboard/static/src/sprint-board/board-render.js` | 94 |
| `apps/dashboard/db.py` | 75 |
| `apps/dashboard/static/index.html` | 75 |
| `apps/dashboard/routers/__init__.py` | 55 |
| `README.md` | 52 |
| `apps/dashboard/startup.py` | 50 |
| `apps/dashboard/static/src/sprint-board/history.js` | 49 |
| `SCHEMA.md` | 48 |
| `docs/architecture/code-state.md` | 46 |
| `apps/dashboard/static/src/sprint-board/index.js` | 42 |
| `apps/dashboard/github_client.py` | 42 |
| `apps/dashboard/routers/sprint_history_service.py` | 39 |
| `services/sprint_manager/estimate_issue.py` | 38 |

## Generated

Sprint: `sprint-1022.3`  
Timestamp: `2026-08-12T19:15:52Z`  
_Generated deterministically — no LLM required._
