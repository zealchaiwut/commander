# Code State — sprint-1022.4

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

## Recent Deltas (sprint-1022.4)

Files changed: **106**

- `tests/` — 59 file(s)
- `apps/` — 24 file(s)
- `services/` — 16 file(s)
- `(root)/` — 3 file(s)
- `docs/` — 2 file(s)
- `scripts/` — 2 file(s)

## Hot Files (last 90 days)

| File | Commits |
|------|---------|
| `apps/dashboard/static/project.html` | 539 |
| `apps/dashboard/server.py` | 401 |
| `services/sprint_manager/sprint_manager.py` | 291 |
| `apps/dashboard/static/dist/bundle.js.map` | 211 |
| `apps/dashboard/static/dist/bundle.js` | 201 |
| `CHANGELOG.md` | 127 |
| `apps/dashboard/static/app.js` | 106 |
| `apps/dashboard/static/src/sprint-board/board-render.js` | 92 |
| `apps/dashboard/db.py` | 75 |
| `apps/dashboard/static/index.html` | 75 |
| `apps/dashboard/routers/__init__.py` | 53 |
| `README.md` | 51 |
| `apps/dashboard/startup.py` | 49 |
| `apps/dashboard/static/src/sprint-board/history.js` | 48 |
| `SCHEMA.md` | 46 |
| `docs/architecture/code-state.md` | 45 |
| `apps/dashboard/github_client.py` | 42 |
| `apps/dashboard/static/src/sprint-board/index.js` | 40 |
| `apps/dashboard/routers/sprint_history_service.py` | 39 |
| `services/sprint_manager/estimate_issue.py` | 38 |

## Generated

Sprint: `sprint-1022.4`  
Timestamp: `2026-08-12T20:10:45Z`  
_Generated deterministically — no LLM required._
