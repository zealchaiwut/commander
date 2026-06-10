"""FastAPI routers extracted from the server.py monolith (issue #761).

This package is the strangler-fig landing zone. New endpoints belong in
``apps/dashboard/routers/<area>.py`` and are mounted on the app via
``app.include_router(...)`` — adding routes directly to ``server.py`` is
forbidden and is rejected by the COMMANDER_GATE_MONOLITH gate.
"""
from .backup import router as backup_router

__all__ = ["backup_router"]
