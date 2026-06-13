"""FastAPI routers extracted from the server.py monolith (issue #761).

This package is the strangler-fig landing zone. New endpoints belong in
``apps/dashboard/routers/<area>.py`` and are mounted on the app via
``app.include_router(...)`` — adding routes directly to ``server.py`` is
forbidden and is rejected by the COMMANDER_GATE_MONOLITH gate.
"""
from .activity import router as activity_router
from .analytics import router as analytics_router
from .backup import router as backup_router
from .doctor import router as doctor_router
from .home_milestone import router as home_milestone_router
from .log_search import router as log_search_router
from .milestones import router as milestones_router
from .roadmap import router as roadmap_router
from .runs import router as runs_router
from .sprint_history import router as sprint_history_router
from .sprints import router as sprints_router
from .status import router as status_router
from .system import router as system_router
from .tickets import router as tickets_router

__all__ = [
    "activity_router",
    "analytics_router",
    "backup_router",
    "doctor_router",
    "home_milestone_router",
    "log_search_router",
    "milestones_router",
    "roadmap_router",
    "runs_router",
    "sprint_history_router",
    "sprints_router",
    "status_router",
    "system_router",
    "tickets_router",
]
