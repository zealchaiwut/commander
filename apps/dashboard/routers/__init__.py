"""FastAPI routers extracted from the server.py monolith (issue #761).

This package is the strangler-fig landing zone. New endpoints belong in
``apps/dashboard/routers/<area>.py`` and are mounted on the app via
``app.include_router(...)`` — adding routes directly to ``server.py`` is
forbidden and is rejected by the COMMANDER_GATE_MONOLITH gate.
"""
from .activity import router as activity_router
from .pages import router as pages_router
from .advisor import router as advisor_router
from .analytics import router as analytics_router
from .logs import router as logs_router
from .backup import router as backup_router
from .doctor import router as doctor_router
from .finish_progress import router as finish_progress_router
from .home_milestone import router as home_milestone_router
from .log_search import router as log_search_router
from .milestones import router as milestones_router
from .roadmap import router as roadmap_router
from .runs import router as runs_router
from .scheduler import router as scheduler_router
from .signoff import router as signoff_router
from .sprint_history import router as sprint_history_router
from .sprints import router as sprints_router
from .status import router as status_router
from .settings import router as settings_router
from .suggestions import router as suggestions_router
from .system import router as system_router
from .tickets import router as tickets_router
from .timeline import router as timeline_router
from .project_branches import router as project_branches_router
from .sprint_crud import router as sprint_crud_router
from .sprint_live import router as sprint_live_router
from .sprint_preflight import router as sprint_preflight_router
from .sprint_summaries import router as sprint_summaries_router
from .system_misc import router as system_misc_router
from .sprint_finish import router as sprint_finish_router
from .sprint_run import router as sprint_run_router
from .bulk_tickets import router as bulk_tickets_router
# Routers extracted from server.py in issue #1267
from .sprint_nav import router as sprint_nav_router
from .issues import router as issues_router
from .projects import router as projects_router
from .environments import router as environments_router
from .settings_sync import router as settings_sync_router
from .maintenance import router as maintenance_router
from .sprint_planning import router as sprint_planning_router
from .sprint_labels import router as sprint_labels_router
from .sprint_dispatch import router as sprint_dispatch_router
from .estimates import router as estimates_router
from .calibration import router as calibration_router
from .metrics import router as metrics_router
from .reports import router as reports_router
from .finish_card import router as finish_card_router
from .deploy import router as deploy_router
from .mis_sizing import router as mis_sizing_router

__all__ = [
    "activity_router",
    "pages_router",
    "advisor_router",
    "analytics_router",
    "logs_router",
    "backup_router",
    "doctor_router",
    "finish_progress_router",
    "home_milestone_router",
    "log_search_router",
    "milestones_router",
    "roadmap_router",
    "runs_router",
    "scheduler_router",
    "signoff_router",
    "sprint_history_router",
    "sprints_router",
    "settings_router",
    "status_router",
    "suggestions_router",
    "system_router",
    "tickets_router",
    "timeline_router",
    "project_branches_router",
    "sprint_crud_router",
    "sprint_live_router",
    "sprint_preflight_router",
    "sprint_summaries_router",
    "system_misc_router",
    "sprint_finish_router",
    "sprint_run_router",
    "bulk_tickets_router",
    "sprint_nav_router",
    "issues_router",
    "projects_router",
    "environments_router",
    "settings_sync_router",
    "maintenance_router",
    "sprint_planning_router",
    "sprint_labels_router",
    "sprint_dispatch_router",
    "estimates_router",
    "calibration_router",
    "metrics_router",
    "reports_router",
    "finish_card_router",
    "deploy_router",
    "mis_sizing_router",
]
