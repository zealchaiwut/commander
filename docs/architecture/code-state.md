# Code State — sprint-1002.4

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

## Recent Deltas (sprint-1002.4)

Files changed: **18**

- `apps/` — 7 file(s)
- `tests/` — 5 file(s)
- `(root)/` — 2 file(s)
- `docs/` — 2 file(s)
- `scripts/` — 1 file(s)
- `services/` — 1 file(s)

## Hot Files (last 90 days)

| File | Commits |
|------|---------|
| `apps/dashboard/static/project.html` | 514 |
| `apps/dashboard/server.py` | 391 |
| `services/sprint_manager/sprint_manager.py` | 271 |
| `apps/dashboard/static/dist/bundle.js.map` | 186 |
| `apps/dashboard/static/dist/bundle.js` | 176 |
| `apps/dashboard/static/app.js` | 106 |
| `CHANGELOG.md` | 99 |
| `apps/dashboard/static/src/sprint-board/board-render.js` | 88 |
| `apps/dashboard/static/index.html` | 75 |
| `apps/dashboard/db.py` | 64 |
| `README.md` | 51 |
| `apps/dashboard/routers/__init__.py` | 48 |
| `apps/dashboard/static/src/sprint-board/history.js` | 46 |
| `SCHEMA.md` | 41 |
| `apps/dashboard/github_client.py` | 40 |
| `apps/dashboard/static/src/sprint-board/index.js` | 38 |
| `apps/dashboard/routers/sprint_history_service.py` | 36 |
| `services/sprint_manager/estimate_issue.py` | 35 |
| `apps/dashboard/startup.py` | 34 |
| `CLAUDE.md` | 30 |

## Generated

Sprint: `sprint-1002.4`  
Timestamp: `2026-07-17T09:48:54Z`  
_Generated deterministically — no LLM required._
