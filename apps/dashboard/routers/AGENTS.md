# AGENTS.md — apps/dashboard/routers

## Purpose

Strangler-fig router modules being extracted from the `server.py` monolith
(issue #761). Each file owns a specific API area and is mounted on the main
FastAPI app via `app.include_router(...)`. Every new endpoint belongs here,
not in `server.py`.

## Key Files

- `__init__.py` — imports and re-exports all routers; **add every new router here**
- `backup.py` — backup-related endpoints (`/api/backup/*`)
- `backup_service.py` — backup business logic, decoupled from route handlers

## Conventions

- One file per API area: `<area>.py` for routes, `<area>_service.py` for logic.
- Router declaration: `router = APIRouter(prefix="/api/<area>", tags=["<area>"])`.
- After creating `<area>.py`, import the router in `__init__.py` and add to `__all__`.
- Then mount in `server.py`: `from apps.dashboard.routers import <area>_router` + `app.include_router(...)`.
- Keep route handlers thin: call into `<area>_service.py` for anything beyond trivial I/O.
- Use Pydantic v2 response models on all endpoints.

## Danger Zones

- Do NOT add routes back to `server.py` — `COMMANDER_GATE_MONOLITH` will fail the PR.
- Do NOT put heavy business logic inline in route handlers — it makes testing hard.
- Do NOT change existing route paths without a versioning plan — callers will break.
- Do NOT use wildcard imports (`from routers import *`) — always name the router.

## What NOT to Touch

- `__init__.py` `__all__` list — always extend it when adding a new router; never shrink it.
- `backup.py` route paths — the backup endpoints are used by scripts; changing paths is breaking.

<!-- needs-review: hotfix/board-history-running-ux — directory had changes; review and update this file -->
