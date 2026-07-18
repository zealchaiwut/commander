# Code State — sprint-104.1

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

## Recent Deltas (sprint-104.1)

Files changed: **382**

- `tests/` — 206 file(s)
- `apps/` — 107 file(s)
- `services/` — 26 file(s)
- `docs/` — 21 file(s)
- `scripts/` — 12 file(s)
- `(root)/` — 8 file(s)
- `.commander/` — 1 file(s)
- `hooks/` — 1 file(s)

## Hot Files (last 90 days)

| File | Commits |
|------|---------|
| `apps/dashboard/static/project.html` | 474 |
| `apps/dashboard/server.py` | 380 |
| `services/sprint_manager/sprint_manager.py` | 247 |
| `apps/dashboard/static/dist/bundle.js.map` | 150 |
| `apps/dashboard/static/dist/bundle.js` | 143 |
| `apps/dashboard/static/app.js` | 106 |
| `apps/dashboard/static/src/sprint-board/board-render.js` | 78 |
| `apps/dashboard/static/index.html` | 75 |
| `CHANGELOG.md` | 72 |
| `apps/dashboard/db.py` | 52 |
| `README.md` | 46 |
| `apps/dashboard/static/src/sprint-board/history.js` | 42 |
| `apps/dashboard/routers/__init__.py` | 41 |
| `apps/dashboard/static/src/sprint-board/index.js` | 35 |
| `apps/dashboard/routers/sprint_history_service.py` | 33 |
| `apps/dashboard/github_client.py` | 33 |
| `SCHEMA.md` | 31 |
| `services/sprint_manager/estimate_issue.py` | 29 |
| `CLAUDE.md` | 26 |
| `dashboard/scripts/sprint_manager.py` | 25 |

## Generated

Sprint: `sprint-104.1`  
Timestamp: `2026-07-18T14:06:36Z`  
_Generated deterministically — no LLM required._
