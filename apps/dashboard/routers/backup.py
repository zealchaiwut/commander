"""Backup endpoints (extracted from server.py, issue #761).

First cluster moved out of the monolith under the strangler-fig plan. Service
logic lives in the sibling ``backup_service`` module.
"""
from fastapi import APIRouter

from . import backup_service

router = APIRouter(tags=["backup"])


@router.get("/api/backup/status")
def get_backup_status():
    """Return the current state of the gist-based config backup."""
    return backup_service.get_backup_status()
